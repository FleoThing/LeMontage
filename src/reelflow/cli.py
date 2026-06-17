"""Reelflow command-line interface: ``run``, ``validate`` and ``init``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .validator import validate_doc, validate_file

STARTER_PIPELINE = """\
reelflow: "1.0"
name: my-pipeline
description: "A starter Reelflow pipeline"

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reelflow", description=__doc__)
    parser.add_argument("--version", action="version", version=f"reelflow {__version__}")
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
        help="delete intermediate/temp files (output/.reelflow) after a successful run",
    )

    p_validate = sub.add_parser("validate", help="validate a pipeline without running it")
    p_validate.add_argument("file", help="pipeline YAML file")

    p_init = sub.add_parser("init", help="write a starter pipeline file")
    p_init.add_argument("file", nargs="?", default="pipeline.yaml", help="output path")
    p_init.add_argument("--force", action="store_true", help="overwrite if the file exists")

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.file)
    if args.command == "init":
        return _cmd_init(args.file, args.force)
    if args.command == "run":
        return _cmd_run(args.file, args.var, args.clean)
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


def _cmd_init(file: str, force: bool) -> int:
    path = Path(file)
    if path.exists() and not force:
        print(f"✗ {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.write_text(STARTER_PIPELINE, encoding="utf-8")
    print(f"✓ wrote starter pipeline to {path}")
    return 0


def _cmd_run(file: str, var_args: list[str], clean: bool = False) -> int:
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
        overrides[key.strip()] = value
    return overrides


# Re-exported so tests can build docs without importing the file path machinery.
__all__ = ["main", "validate_doc", "STARTER_PIPELINE"]
