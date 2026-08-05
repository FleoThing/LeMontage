"""``overlay`` — conditional title/band/image overlay on a clip (SPEC §6.12).

Draws multi-line text — optionally on a uniform full-width colour band — over a
time window of the clip. The band is an FFmpeg ``drawbox`` gated with
``enable='between(t,from,to)'``; the text reuses the export title's libass
plumbing (the static FFmpeg build ships no ``drawtext``), with the ASS
Dialogue start/end providing the same window.

``text`` also takes a list of ``{text, color}`` runs, for a paragraph whose
keywords each carry their own colour. The pipeline names colours; this module
emits the ASS override blocks. User text keeps going through
:func:`~lemontage.engine.assformat.escape_text` untouched, so the escaping that
stops an untrusted pipeline from injecting render directives still holds.

An ``image`` composites a prepared graphic (a transparent PNG: a logo, a
lower-third, a whole header card) at a pixel position, gated by the same window.
Text and libass can only ever draw glyphs, so anything with real artwork in it
had to be baked into the source before this. The image is used at its own size —
author it at the frame's resolution rather than asking the block to rescale it.
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


def _enable(start: float, end: float | None) -> str:
    """The FFmpeg ``enable=`` gate for the show window, or "" for the whole clip."""
    if end is None and start <= 0:
        return ""
    return f":enable='between(t,{start:g},{end if end is not None else 1e9:g})'"


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
    return box + _enable(start, end)


def _image_path(value: object) -> str:
    """The ``image`` file, checked up front so FFmpeg doesn't fail obscurely."""
    path = Path(str(value))
    if not path.is_file():
        raise ValueError(f"overlay: image '{value}' not found")
    return str(path)


def _image_xy(params: dict[str, Any]) -> str:
    """``x:y`` for the image's top-left corner.

    A negative value counts back from the opposite edge (``x: -40`` = 40px in
    from the right), which is how a corner watermark is expressed without
    knowing the frame width. Both are integers we format into the expression
    ourselves — the pipeline never supplies filtergraph syntax.
    """
    parts = []
    for key, frame, img in (("x", "W", "w"), ("y", "H", "h")):
        value = params.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"overlay: {key} must be an integer (pixels)")
        parts.append(str(value) if value >= 0 else f"{frame}-{img}{value}")
    return ":".join(parts)


def _image_graph(band: list[str], text: list[str], xy: str, gate: str) -> tuple[str, str]:
    """The ``filter_complex`` compositing the image, and the label to map.

    Order is band → image → text, so a caption stays readable on top of the
    artwork rather than under it.
    """
    steps, current = [], "[0:v]"
    if band:
        steps.append(f"{current}{','.join(band)}[banded]")
        current = "[banded]"
    steps.append(f"{current}[1:v]overlay={xy}{gate}[composited]")
    current = "[composited]"
    if text:
        steps.append(f"{current}{','.join(text)}[out]")
        current = "[out]"
    return ";".join(steps), current


def _ass_lines(raw: str, strip: bool) -> str:
    """Escape one chunk of user text and join its lines with ASS breaks.

    ``strip`` trims each line — right for a whole-overlay string, wrong for an
    inline run, where the author's spaces are what separate it from its
    neighbours.
    """
    text = raw.replace("\\n", "\n")
    lines = [escape_text(line) for line in text.splitlines() or [""]]
    if strip:
        lines = [line.strip() for line in lines if line.strip()]
    return r"\N".join(lines)


def _ass_body(text: object) -> str:
    """The ASS Dialogue text: a plain string, or ``{text, color}`` runs.

    Runs are what a multi-colour paragraph needs — the highlighted-keyword look
    where each phrase carries its own colour. The pipeline never writes ASS: it
    names a colour, :func:`_ass_color` accepts only six hex digits or a known
    name, and this function emits the override block. The run's own text still
    goes through :func:`escape_text`, so its braces and backslashes are
    neutralised and the tags below are the only ones libass can ever see.

    Runs are concatenated verbatim, spaces included — ``{text: "Apollo 11's"}``
    followed by ``{text: " flag"}`` is how the two stay apart.
    """
    if isinstance(text, str):
        body = _ass_lines(text, strip=True)
        if not body:
            raise ValueError("overlay: 'text' is empty")
        return body
    if not isinstance(text, list) or not text:
        raise ValueError("overlay: 'text' must be a string or a list of {text, color} runs")

    parts = []
    for index, run in enumerate(text):
        if not isinstance(run, dict) or not str(run.get("text", "")).strip():
            raise ValueError(f"overlay: text run {index} needs a non-empty 'text'")
        body = _ass_lines(str(run["text"]), strip=False)
        color = run.get("color")
        if color:
            # `\c` wants the &H..& override form; _ass_color returns the bare
            # Style-field form, so the closing & is added here.
            ass = _ass_color(color, f"overlay.text[{index}].color")
            body = f"{{\\c{ass}&}}{body}{{\\r}}"
        parts.append(body)
    return "".join(parts)


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
    body = _ass_body(params["text"])
    line_count = body.count("\\N") + 1

    band = params.get("band")
    position = str(band.get("position", "top")).lower() if isinstance(band, dict) else "top"
    align = 8 if position == "top" else 2
    if isinstance(band, dict):
        # Centre the text block vertically inside the band.
        band_h = int(band.get("height", _DEFAULT_BAND_HEIGHT))
        margin = max((band_h - text_size * line_count) // 2, 0)
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
            text=body,
            start=_ass_timestamp(start),
            end=_ass_timestamp(end) if end is not None else _TITLE_FOREVER,
            primary=_ass_color(params.get("color"), "overlay.color"),
            align=align,
            border=1,
            outline="&H00000000",
            outline_w=0,
            shadow=0,
        )
    )
    return path


def _overlay(media: str, params: dict[str, Any], ctx: RunContext, name: str, out: Path) -> None:
    image = params.get("image")
    if not params.get("text") and not image:
        raise ValueError("overlay: needs a 'text' and/or an 'image'")
    start, end = _show_window(params)
    size_wh = ffmpeg.probe_resolution(media)

    band_chain: list[str] = []
    band = params.get("band")
    if band is not None:
        if not isinstance(band, dict):
            raise ValueError("overlay: 'band' must be a mapping (color/height/position)")
        band_chain.append(_band_filter(band, size_wh[1], start, end))

    text_chain: list[str] = []
    if params.get("text"):
        fonts.ensure(params.get("font"))
        text_chain.append(fonts.libass_filter(_text_ass(params, ctx, name, size_wh, start, end)))

    if image:
        # A second input means a filter_complex, so the streams need mapping by
        # hand; `0:a?` keeps the audio when the clip has one and skips it when
        # it doesn't (a `still` clip is video-only).
        graph, label = _image_graph(band_chain, text_chain, _image_xy(params), _enable(start, end))
        args = [
            "-i", str(media),
            "-i", _image_path(image),
            "-filter_complex", graph,
            "-map", label,
            "-map", "0:a?",
        ]  # fmt: skip
    else:
        args = ["-i", str(media), "-vf", ",".join(band_chain + text_chain)]

    ffmpeg.run([*args, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out)])
