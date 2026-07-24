"""``filter`` — per-clip looks: grade and stylise a clip (SPEC §6.13).

Works on the pipeline input (single mode) or maps over a channel of clips. A
``look`` (or a list of looks, applied in order) picks a named FFmpeg effect —
``bw``, ``vignette``, ``grain``, ``sharpen`` — and an ``eq`` mapping grades
colour (brightness / contrast / saturation / gamma). Everything is one FFmpeg
video-filter chain, so no extra dependency.
"""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from .base import Block, BlockResult, ItemResult

# Named looks → their FFmpeg filter. Defaults are tuned to read on a phone
# without wrecking the footage; `eq` is the knob for fine colour control.
LOOKS = {
    "bw": "hue=s=0",  # desaturate to black & white
    "vignette": "vignette=PI/5",  # darkened corners
    "grain": "noise=alls=12:allf=t",  # temporal film grain
    "sharpen": "unsharp=5:5:1.0:5:5:0.0",  # luma sharpen
}

# `eq` sub-keys → the eq= option name. Each is a plain number.
_EQ_KEYS = {
    "brightness": "brightness",
    "contrast": "contrast",
    "saturation": "saturation",
    "gamma": "gamma",
}


class FilterBlock(Block):
    name = "filter"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("filter: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _apply(media, _chain(params), out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip") or item.get("file")
        if clip is None:
            raise ValueError("filter: channel item has no 'clip' (run 'cut' first)")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _apply(clip, _chain(params), out)
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _chain(params: dict[str, Any]) -> str:
    """Build the FFmpeg -vf chain from ``look`` (name or list) and ``eq``."""
    filters: list[str] = []
    eq = _eq(params.get("eq"))
    if eq:
        filters.append(eq)
    for name in _looks(params.get("look")):
        if name not in LOOKS:
            valid = ", ".join(sorted(LOOKS))
            raise ValueError(f"filter: unknown look '{name}' (choose from: {valid})")
        filters.append(LOOKS[name])
    if not filters:
        raise ValueError("filter: nothing to do — set 'look' and/or 'eq'")
    return ",".join(filters)


def _looks(look: Any) -> list[str]:
    if look is None:
        return []
    if isinstance(look, str):
        return [look]
    if isinstance(look, list):
        return [str(name) for name in look]
    raise ValueError("filter: 'look' must be a name or a list of names")


def _eq(eq: Any) -> str | None:
    if eq is None:
        return None
    if not isinstance(eq, dict):
        raise ValueError("filter: 'eq' must be a mapping (brightness/contrast/saturation/gamma)")
    parts = []
    for key, value in eq.items():
        if key not in _EQ_KEYS:
            valid = ", ".join(sorted(_EQ_KEYS))
            raise ValueError(f"filter: unknown eq key '{key}' (choose from: {valid})")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"filter: eq.{key} must be a number")
        parts.append(f"{_EQ_KEYS[key]}={value}")
    return f"eq={':'.join(parts)}" if parts else None


def _apply(media: str, chain: str, out) -> None:
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-vf",
            chain,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "copy",
            str(out),
        ]
    )
