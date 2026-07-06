"""``speed`` — retime a clip: slow-motion or fast-forward (SPEC §6.8).

Works on the pipeline input (single mode) or maps over a channel of clips.
``factor`` is the playback multiplier: ``2`` plays twice as fast, ``0.5`` is
half-speed slow-motion. Video timestamps are rescaled with ``setpts`` and audio
with ``atempo`` (chained so any factor outside FFmpeg's 0.5–2.0 per-filter range
still works).
"""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from .base import Block, BlockResult, ItemResult


class SpeedBlock(Block):
    name = "speed"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("speed: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _retime(media, _factor(params), out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip")
        if clip is None:
            raise ValueError("speed: channel item has no 'clip' (run 'cut' first)")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _retime(clip, _factor(params), out)
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


_MAX_FACTOR = 100.0  # bounds the atempo chain length; well beyond any real use


def _factor(params: dict[str, Any]) -> float:
    factor = float(params.get("factor", 1.0))
    if factor <= 0:
        raise ValueError("speed: 'factor' must be > 0 (e.g. 2 = 2x faster, 0.5 = slow-motion)")
    if factor > _MAX_FACTOR:
        raise ValueError(f"speed: 'factor' must be <= {_MAX_FACTOR:g}")
    return factor


def _atempo_chain(factor: float) -> str:
    """Express any positive factor as a chain of atempo filters (each 0.5–2.0)."""
    remaining = factor
    steps: list[str] = []
    while remaining > 2.0:
        steps.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        steps.append("atempo=0.5")
        remaining /= 0.5
    steps.append(f"atempo={remaining:.6f}")
    return ",".join(steps)


def _retime(media: str, factor: float, out) -> None:
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-vf",
            f"setpts={1 / factor:.6f}*PTS",
            "-af",
            _atempo_chain(factor),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
