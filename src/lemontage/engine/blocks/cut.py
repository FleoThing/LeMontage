"""``cut`` — extract a segment, or map over a channel of clips (SPEC §6.4)."""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult


class CutBlock(Block):
    name = "cut"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("cut: no input media")
        if "start" not in params or "end" not in params:
            raise ValueError("cut: 'start' and 'end' are required when not mapping a channel")
        start = parse_seconds(params["start"])
        end = parse_seconds(params["end"])
        out = ctx.work_dir() / f"{step_id}.mp4"
        _cut_segment(media, start, end, out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("cut: no input media")
        start = parse_seconds(item["start"])
        end = parse_seconds(item["end"])
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _cut_segment(media, start, end, out)
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _cut_segment(media: str, start: float, end: float, out) -> None:
    duration = max(0.0, end - start)
    # Seek before -i for speed; use -t (duration) to avoid -to ambiguity with input seeking.
    ffmpeg.run(
        [
            "-ss",
            f"{start:.3f}",
            "-i",
            str(media),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
