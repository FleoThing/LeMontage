"""``overlay`` — conditional title/band overlay on a clip (SPEC §6.12).

Draws multi-line text — optionally on a uniform full-width colour band — over a
time window of the clip. The band is an FFmpeg ``drawbox`` gated with
``enable='between(t,from,to)'``; the text reuses the export title's libass
plumbing (the static FFmpeg build ships no ``drawtext``), with the ASS
Dialogue start/end providing the same window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...spec import OVERLAY_BAND_POSITIONS
from .. import ffmpeg, fonts
from ..assformat import escape_text
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult
from .export import _ASS_TEMPLATE, _TITLE_FOREVER, _ass_color, _ass_timestamp, _bg_pad_color

_DEFAULT_SIZE = 72
_DEFAULT_BAND_HEIGHT = 210
_DEFAULT_MARGIN = 60


class OverlayBlock(Block):
    name = "overlay"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("overlay: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _overlay(media, params, ctx, step_id, out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip")
        if clip is None:
            raise ValueError("overlay: channel item has no 'clip' (run 'cut' first)")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _overlay(clip, params, ctx, f"{step_id}-{item['index']}", out)
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _show_window(params: dict[str, Any]) -> tuple[float, float | None]:
    """The (start, end) seconds the overlay is visible; end None = whole clip."""
    show = params.get("show")
    if show is None:
        return 0.0, None
    if not isinstance(show, dict):
        raise ValueError("overlay: 'show' must be a mapping with 'from'/'to'")
    if "except" in show:
        raise ValueError("overlay: show.except is not supported yet (use show.from/show.to)")
    start = parse_seconds(show.get("from", 0))
    end = parse_seconds(show["to"]) if "to" in show else None
    if end is not None and end <= start:
        raise ValueError("overlay: show.to must be after show.from")
    return start, end


def _band_filter(band: dict[str, Any], height: int, start: float, end: float | None) -> str:
    """A full-width ``drawbox`` band, gated to the show window."""
    band_h = int(band.get("height", _DEFAULT_BAND_HEIGHT))
    if band_h <= 0:
        raise ValueError("overlay: band.height must be > 0")
    position = str(band.get("position", "top")).lower()
    if position not in OVERLAY_BAND_POSITIONS:
        valid = ", ".join(sorted(OVERLAY_BAND_POSITIONS))
        raise ValueError(f"overlay: unknown band.position '{position}' (choose from: {valid})")
    y = 0 if position == "top" else height - band_h
    color = _bg_pad_color(band.get("color", "black"))
    box = f"drawbox=x=0:y={y}:w=iw:h={band_h}:color={color}:t=fill"
    if end is not None or start > 0:
        box += f":enable='between(t,{start:g},{end if end is not None else 1e9:g})'"
    return box


def _text_ass(
    params: dict[str, Any],
    ctx: RunContext,
    name: str,
    size_wh: tuple[int, int],
    start: float,
    end: float | None,
) -> Path:
    """Write the ASS file for the overlay text (reuses the export title style)."""
    width, height = size_wh
    text_size = int(params.get("size", _DEFAULT_SIZE))
    if text_size <= 0:
        raise ValueError("overlay: size must be > 0")
    font = fonts.family(params.get("font"))
    raw = str(params["text"])
    lines = [escape_text(ln.strip()) for ln in raw.replace("\\n", "\n").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("overlay: 'text' is empty")

    band = params.get("band")
    position = str(band.get("position", "top")).lower() if isinstance(band, dict) else "top"
    align = 8 if position == "top" else 2
    if isinstance(band, dict):
        # Centre the text block vertically inside the band.
        band_h = int(band.get("height", _DEFAULT_BAND_HEIGHT))
        margin = max((band_h - text_size * len(lines)) // 2, 0)
    else:
        margin = int(params.get("margin", _DEFAULT_MARGIN))

    path = ctx.work_dir() / f"{name}.ass"
    path.write_text(
        _ASS_TEMPLATE.format(
            w=width,
            h=height,
            font=font,
            size=text_size,
            margin=margin,
            text=r"\N".join(lines),
            start=_ass_timestamp(start),
            end=_ass_timestamp(end) if end is not None else _TITLE_FOREVER,
            primary=_ass_color(params.get("color")),
            align=align,
            border=1,
            outline="&H00000000",
            outline_w=0,
            shadow=0,
        )
    )
    return path


def _overlay(media: str, params: dict[str, Any], ctx: RunContext, name: str, out: Path) -> None:
    if not params.get("text"):
        raise ValueError("overlay: 'text' is required")
    start, end = _show_window(params)
    size_wh = ffmpeg.probe_resolution(media)
    chain: list[str] = []
    band = params.get("band")
    if band is not None:
        if not isinstance(band, dict):
            raise ValueError("overlay: 'band' must be a mapping (color/height/position)")
        chain.append(_band_filter(band, size_wh[1], start, end))
    fonts.ensure(params.get("font"))
    chain.append(fonts.libass_filter(_text_ass(params, ctx, name, size_wh, start, end)))
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-vf",
            ",".join(chain),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
