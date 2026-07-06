"""``still`` — render a static image into a short video clip (SPEC §6.11).

Maps over a ``stills`` channel: each image becomes a ``duration``-second clip so
the existing ``export`` (format/fit/title) and ``concat`` (transitions) blocks
can treat it like any other clip. The clip is **video-only** — no audio track is
synthesised — so a downstream ``concat`` must tolerate silent clips.
"""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult

_DEFAULT_DURATION = 3.0
_DEFAULT_FPS = 30


class StillBlock(Block):
    name = "still"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        image = params.get("image") or params.get("input")
        if not image:
            raise ValueError("still: no image (map a 'stills' channel, or set 'image')")
        duration = parse_seconds(params.get("duration", _DEFAULT_DURATION))
        out = ctx.work_dir() / f"{step_id}.mp4"
        _render_still(str(image), duration, out, int(params.get("fps", _DEFAULT_FPS)))
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        image = item.get("image")
        if not image:
            raise ValueError("still: channel item has no 'image' (run 'stills' first)")
        duration = parse_seconds(item.get("duration", params.get("duration", _DEFAULT_DURATION)))
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _render_still(str(image), duration, out, int(params.get("fps", _DEFAULT_FPS)))
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _render_still(image: str, duration: float, out, fps: int) -> None:
    # Loop a single image for `duration` seconds into an H.264 clip. yuv420p keeps
    # it broadly playable; the scale rounds to even dimensions (libx264 requires
    # even width/height). No audio track is added.
    ffmpeg.run(
        [
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image),
            "-r",
            str(fps),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
