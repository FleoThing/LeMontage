"""``concat`` — stitch a channel's clips into a single video (SPEC §6.7).

A channel *aggregator* (``maps = False``): instead of running once per item, it
receives the whole channel and joins the clips in order into one file. Place it
after ``export`` to assemble the rendered clips into a final reel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..context import RunContext
from .base import Block, BlockResult


class ConcatBlock(Block):
    name = "concat"
    maps = False

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        raise ValueError("concat: requires 'from: <channel>'")

    def execute_channel(
        self, params: dict[str, Any], items: list[dict[str, Any]], ctx: RunContext, step_id: str
    ) -> BlockResult:
        ordered = sorted(items, key=lambda it: it.get("index", 0))
        # Prefer the exported file; fall back to the cut/captioned clip.
        files = [it.get("file") or it.get("clip") for it in ordered]
        files = [f for f in files if f]
        if not files:
            return BlockResult(outputs={})
        out = _output_path(params, ctx)
        _concat(files, out, ctx.work_dir() / f"{step_id}-list.txt")
        return BlockResult(outputs={"file": str(out), "parts": files})


def _output_path(params: dict[str, Any], ctx: RunContext) -> Path:
    template = params.get("output")
    if template:
        rendered = (
            str(template)
            .replace("{{ name }}", ctx.pipeline_name)
            .replace("{{name}}", ctx.pipeline_name)
        )
        out = Path(rendered)
    else:
        out = ctx.output_dir / f"{ctx.pipeline_name}-reel.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _concat(files: list[str], out: Path, list_path: Path) -> None:
    # concat demuxer: a text file of `file '<abs path>'` lines, then re-encode
    # (clips share the same export settings, but re-encoding avoids timestamp glitches).
    lines = [f"file '{Path(f).resolve()}'" for f in files]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg.run(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
