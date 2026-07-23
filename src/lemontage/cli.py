"""LeMontage command-line interface: ``run``, ``validate``, ``init`` and ``completion``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .validator import validate_doc, validate_file

STARTER_PIPELINE = """\
lemontage: "1.0"
name: my-pipeline
description: "A starter LeMontage pipeline"

input:
  type: video
  source: ./video-example.mp4

steps:
  - id: transcript
    stt:
      model: base
      lang: auto

  - id: clips
    detect_clips:
      method: silence
      max_clips: 5
      emit: clip_channel

  - cut:
      from: clip_channel

  - captions:
      from: clip_channel
      words: "{{ steps.transcript.words }}"
      style: tiktok

  - export:
      from: clip_channel
      format: vertical

output:
  dir: ./output
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Shared by main() and the completion generator."""
    parser = argparse.ArgumentParser(prog="lemontage", description=__doc__)
    parser.add_argument("--version", action="version", version=f"lemontage {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a pipeline")
    p_run.add_argument("file", help="pipeline YAML file")
    p_run.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a value from the 'vars' block (repeatable)",
    )
    p_run.add_argument(
        "--clean",
        action="store_true",
        help="delete intermediate/temp files (output/.lemontage) after a successful run",
    )
    p_run.add_argument(
        "--json",
        action="store_true",
        help="print every step's outputs (e.g. the stt transcript) as JSON on stdout, "
        "so an AI agent can read them and choose clips",
    )

    p_analyze = sub.add_parser(
        "analyze",
        help="analyze a video into a compact JSON manifest (VSO) an AI agent reads "
        "instead of screenshotting the video frame by frame",
    )
    p_analyze.add_argument("file", help="video file to analyze")
    p_analyze.add_argument(
        "-o", "--output", help="write the manifest here (default: stdout)", metavar="FILE"
    )
    p_analyze.add_argument(
        "--no-transcribe",
        action="store_true",
        help="skip speech-to-text (faster; omits speech.words)",
    )
    p_analyze.add_argument("--model", default="base", help="whisper model size (default: base)")
    p_analyze.add_argument("--lang", default="auto", help="speech language (default: auto)")

    p_validate = sub.add_parser("validate", help="validate a pipeline without running it")
    p_validate.add_argument("file", help="pipeline YAML file")

    p_init = sub.add_parser("init", help="write a starter pipeline file")
    p_init.add_argument("file", nargs="?", default="pipeline.yaml", help="output path")
    p_init.add_argument("--force", action="store_true", help="overwrite if the file exists")

    p_completion = sub.add_parser(
        "completion", help="print a shell completion script (bash, zsh or fish)"
    )
    p_completion.add_argument("shell", choices=("bash", "zsh", "fish"), help="target shell")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.file)
    if args.command == "init":
        return _cmd_init(args.file, args.force)
    if args.command == "analyze":
        return _cmd_analyze(args.file, args.output, args.no_transcribe, args.model, args.lang)
    if args.command == "run":
        return _cmd_run(args.file, args.var, args.clean, args.json)
    if args.command == "completion":
        return _cmd_completion(args.shell)
    parser.print_help()
    return 1


def _cmd_validate(file: str) -> int:
    errors = validate_file(file)
    if errors:
        print(f"✗ {file}: {len(errors)} error(s)", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"✓ {file}: valid")
    return 0


def _cmd_analyze(
    file: str, output: str | None, no_transcribe: bool, model: str, lang: str
) -> int:
    import json

    from .analyze import analyze_video

    try:
        manifest = analyze_video(file, transcribe=not no_transcribe, model=model, lang=lang)
    except Exception as exc:  # noqa: BLE001 - surface ffmpeg/whisper errors to the user
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"✓ wrote manifest to {output}", file=sys.stderr)
    else:
        print(text)
    return 0


def _cmd_completion(shell: str) -> int:
    from .completion import completion_script

    print(completion_script(shell, build_parser()))
    return 0


def _cmd_init(file: str, force: bool) -> int:
    path = Path(file)
    if path.exists() and not force:
        print(f"✗ {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.write_text(STARTER_PIPELINE, encoding="utf-8")
    print(f"✓ wrote starter pipeline to {path}")
    return 0


def _cmd_run(file: str, var_args: list[str], clean: bool = False, as_json: bool = False) -> int:
    import yaml

    from .engine import run_pipeline

    errors = validate_file(file)
    if errors:
        print(f"✗ {file}: {len(errors)} error(s)", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    try:
        overrides = _parse_var_overrides(var_args)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    doc = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
    print(f"▶ running {doc.get('name', file)}", file=sys.stderr)
    # --clean forces cleanup; otherwise defer to the pipeline's output.cleanup.
    clean_override = True if clean else None
    try:
        result = run_pipeline(doc, var_overrides=overrides, clean=clean_override)
    except Exception as exc:  # noqa: BLE001 - surface engine errors to the user
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    if as_json:
        import json

        payload = {
            "ok": result.ok,
            "cells": [
                {"matrix": c.matrix, "states": c.states, "outputs": c.outputs} for c in result.cells
            ],
        }
        print(json.dumps(payload, default=str))

    if result.ok:
        print(f"✓ {file}: done ({len(result.cells)} run(s))", file=sys.stderr)
        return 0
    print(f"✗ {file}: pipeline finished with failures", file=sys.stderr)
    return 1


def _parse_var_overrides(var_args: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for raw in var_args:
        if "=" not in raw:
            raise ValueError(f"--var expects KEY=VALUE, got '{raw}'")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--var has an empty key: '{raw}'")
        # `vars` is a flat mapping; a dotted key would create an entry no
        # template reference ({{ vars.<key> }}) could ever resolve.
        if "." in key:
            raise ValueError(f"--var key '{key}' must not contain '.'")
        overrides[key] = value
    return overrides


# Re-exported so tests can build docs without importing the file path machinery.
__all__ = ["main", "build_parser", "validate_doc", "STARTER_PIPELINE"]
