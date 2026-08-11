"""``reverse`` — play a clip backwards (SPEC §6.9).

Reverses both video and audio. Operates on the pipeline input (single mode) or
maps over a channel of clips. FFmpeg's ``reverse`` filter buffers the stream in
memory, so this is meant for short clips (the usual reel length), not whole
features.
"""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from .base import Block, BlockResult, ItemResult, current_clip


class ReverseBlock(Block):
    name = "reverse"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("reverse: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _reverse(media, out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        key, clip = current_clip(item, "reverse")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _reverse(clip, out)
        return ItemResult(item={key: str(out)}, outputs={"clips": str(out)})


def _reverse(media: str, out) -> None:
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-vf",
            "reverse",
            "-af",
            "areverse",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
