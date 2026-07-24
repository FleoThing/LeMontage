"""LeMontage command-line interface: ``run``, ``analyze``, ``validate`` and ``init``.

Built on [Typer](https://typer.tiangolo.com) (typed sub-commands, ``--help``,
native shell completion via ``--install-completion``) with
[Rich](https://github.com/Textualize/rich) for coloured terminal output. The
``main(argv)`` wrapper keeps returning an exit code so the tests (and any
embedder) can call it directly; machine-readable output (``run --json``,
``analyze`` to stdout) stays plain on **stdout** while status goes to **stderr**.
"""

from __future__ import annotations

from pathlib import Path

import click
import typer
from rich.console import Console

from . import __version__
from .validator import validate_doc, validate_file

# Status/diagnostics on stderr (so stdout stays clean for --json); Rich colours.
err = Console(stderr=True)

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

app = typer.Typer(
    help="LeMontage — pipeline-first clips, reels, TikToks and shorts.",
    add_completion=True,
    no_args_is_help=False,  # no command → a usage error (exit 2), like before
)


def _version(value: bool) -> None:
    if value:
        print(f"lemontage {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="show the version and exit"
    ),
) -> None:
    """LeMontage command-line interface."""


@app.command()
def run(
    file: str = typer.Argument(..., help="pipeline YAML file"),
    var: list[str] = typer.Option(
        [], "--var", metavar="KEY=VALUE", help="override a value from the 'vars' block (repeatable)"
    ),
    clean: bool = typer.Option(
        False, "--clean", help="delete intermediate/temp files after a successful run"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="print every step's outputs as JSON on stdout (for an AI agent)"
    ),
) -> None:
    """Run a pipeline."""
    raise typer.Exit(_cmd_run(file, var, clean, json_output))


@app.command()
def analyze(
    file: str = typer.Argument(..., help="video file to analyze"),
    output: str | None = typer.Option(
        None, "-o", "--output", metavar="FILE", help="write the manifest here (default: stdout)"
    ),
    no_transcribe: bool = typer.Option(False, "--no-transcribe", help="skip speech-to-text"),
    visual: bool = typer.Option(
        False, "--visual", help="score per-shot motion + sharpness (needs the [analyze] extra)"
    ),
    model: str = typer.Option("base", help="whisper model size"),
    lang: str = typer.Option("auto", help="speech language"),
) -> None:
    """Analyze a video into a compact JSON manifest (VSO) an AI agent reads."""
    raise typer.Exit(_cmd_analyze(file, output, no_transcribe, visual, model, lang))


@app.command()
def validate(file: str = typer.Argument(..., help="pipeline YAML file")) -> None:
    """Validate a pipeline without running it."""
    raise typer.Exit(_cmd_validate(file))


@app.command()
def init(
    file: str = typer.Argument("pipeline.yaml", help="output path"),
    force: bool = typer.Option(False, "--force", help="overwrite if the file exists"),
) -> None:
    """Write a starter pipeline file."""
    raise typer.Exit(_cmd_init(file, force))


# A Click command so main() can invoke the app programmatically and read the
# exit code (Typer's app() would sys.exit); no_args_is_help off → "Missing
# command" is a usage error, preserving the pre-Typer behaviour.
_command = typer.main.get_command(app)


def main(argv: list[str] | None = None) -> int:
    # standalone_mode=False makes Click *return* a command's exit code (from a
    # typer.Exit / --version) instead of sys.exit-ing, so we can hand it back.
    try:
        rv = _command(args=argv, standalone_mode=False)
    except SystemExit as exc:
        return int(exc.code or 0)
    except click.exceptions.Abort:
        return 130
    except click.exceptions.UsageError as exc:
        exc.show()  # writes usage + message to stderr
        # No/invalid command exits (like argparse's required subcommand).
        raise SystemExit(exc.exit_code) from exc
    return rv if isinstance(rv, int) else 0


def _cmd_validate(file: str) -> int:
    errors = validate_file(file)
    if errors:
        err.print(f"[bold red]✗[/] {file}: [red]{len(errors)} error(s)[/]")
        for e in errors:
            err.print(f"  [red]-[/] {e}")
        return 1
    err.print(f"[bold green]✓[/] {file}: valid")
    return 0


def _cmd_analyze(
    file: str, output: str | None, no_transcribe: bool, visual: bool, model: str, lang: str
) -> int:
    import json

    from .analyze import analyze_video

    try:
        manifest = analyze_video(
            file, transcribe=not no_transcribe, visual=visual, model=model, lang=lang
        )
    except Exception as exc:  # noqa: BLE001 - surface ffmpeg/whisper errors to the user
        err.print(f"[bold red]✗[/] {exc}")
        return 1

    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        err.print(f"[bold green]✓[/] wrote manifest to {output}")
    else:
        print(text)
    return 0


def _cmd_init(file: str, force: bool) -> int:
    path = Path(file)
    if path.exists() and not force:
        err.print(f"[bold red]✗[/] {path} already exists (use [bold]--force[/] to overwrite)")
        return 1
    path.write_text(STARTER_PIPELINE, encoding="utf-8")
    err.print(f"[bold green]✓[/] wrote starter pipeline to {path}")
    return 0


def _cmd_run(file: str, var_args: list[str], clean: bool = False, as_json: bool = False) -> int:
    import yaml

    from .engine import run_pipeline

    errors = validate_file(file)
    if errors:
        err.print(f"[bold red]✗[/] {file}: [red]{len(errors)} error(s)[/]")
        for e in errors:
            err.print(f"  [red]-[/] {e}")
        return 1

    try:
        overrides = _parse_var_overrides(var_args)
    except ValueError as exc:
        err.print(f"[bold red]✗[/] {exc}")
        return 1

    doc = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
    name = doc.get("name", file)
    clean_override = True if clean else None  # else defer to output.cleanup
    err.print(f"[bold cyan]▶[/] running [bold]{name}[/]")
    try:
        result = run_pipeline(
            doc, var_overrides=overrides, clean=clean_override, reporter=_report_step
        )
    except Exception as exc:  # noqa: BLE001 - surface engine errors to the user
        err.print(f"[bold red]✗[/] {exc}")
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
        err.print(f"[bold green]✓[/] {file}: done ([bold]{len(result.cells)}[/] run(s))")
        return 0
    err.print(f"[bold red]✗[/] {file}: pipeline finished with failures")
    return 1


# Colour the executor's per-step status markers as they stream through.
_MARKER_STYLES = {
    "✓": "green",
    "✗": "bold red",
    "↻": "yellow",
    "⊘": "dim",
    "⊙": "dim cyan",
    "→": "cyan",
    "🧹": "dim",
    "━": "bold magenta",
}


def _report_step(message: str) -> None:
    """Reporter passed to the engine: tint the line by its leading status marker.

    ``markup=False`` so a step's arbitrary text (e.g. an exception with ``[..]``)
    is never parsed as Rich markup.
    """
    marker = message.lstrip()[:1]
    err.print(message, style=_MARKER_STYLES.get(marker), markup=False, highlight=False)


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


__all__ = ["main", "app", "validate_doc", "STARTER_PIPELINE"]
