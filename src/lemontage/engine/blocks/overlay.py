"""``overlay`` — conditional title/band/image overlay on a clip (SPEC §6.12).

Draws multi-line text — optionally on a uniform full-width colour band — over a
time window of the clip. The band is an FFmpeg ``drawbox`` gated with
``enable='between(t,from,to)'``; the text reuses the export title's libass
plumbing (the static FFmpeg build ships no ``drawtext``), with the ASS
Dialogue start/end providing the same window.

``text`` also takes a list of ``{text, color, size, font}`` runs, for a paragraph
whose phrases each carry their own colour — or their own size, which is what a
big rank number followed by a small label needs. The pipeline names colours and
fonts; this module emits the ASS override blocks. User text keeps going through
:func:`~lemontage.engine.assformat.escape_text` untouched, so the escaping that
stops an untrusted pipeline from injecting render directives still holds.

``position`` seats the text at any of the nine frame anchors, so a list can sit
flush left and a credit can sit at the bottom without a band under it, and
``outline`` gives it the black contour it needs to stay readable over footage
rather than over a band.

``cues`` carries several texts, each with its own window and its own style, in
**one** render pass: they become several Dialogue lines of a single ASS file
rather than one filter chain (and one re-encode) per text.

An ``image`` composites a prepared graphic (a transparent PNG: a logo, a
lower-third, a whole header card) at a pixel position, gated by the same window.
Text and libass can only ever draw glyphs, so anything with real artwork in it
had to be baked into the source before this. The image is used at its own size —
author it at the frame's resolution rather than asking the block to rescale it.
A **list** of images picks one per clip by position when mapping a channel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...spec import OVERLAY_BAND_POSITIONS, TEXT_POSITIONS
from .. import ffmpeg, fonts
from ..assformat import document, escape_text
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult, current_clip
from .export import _ass_color, _bg_pad_color

_DEFAULT_SIZE = 72
_DEFAULT_BAND_HEIGHT = 210
_DEFAULT_MARGIN = 60
# Horizontal breathing room, in px, from the frame edge the text is anchored to.
# 40 is what every ASS style in the engine has always used.
_DEFAULT_MARGIN_X = 40
# Letter outline thickness. 0 keeps the flat look overlay text has always had —
# right over a solid band or a card, unreadable over moving footage, which is
# what `outline` is for.
_DEFAULT_OUTLINE = 0


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
        key, clip = current_clip(item, "overlay")
        index = item["index"]
        # `image` as a list is read by clip position, like `export.title_color`:
        # a badge on the first two clips and nothing after is a list of two. With
        # nothing left to draw on this clip, the clip passes through untouched
        # rather than paying for a re-encode that would change nothing.
        if _image_for(params, index) is None and not params.get("text") and not params.get("cues"):
            return ItemResult(item={key: str(clip)}, outputs={"clips": str(clip)})
        out = ctx.work_dir() / f"{step_id}-{index}.mp4"
        _overlay(clip, params, ctx, f"{step_id}-{index}", out, index=index)
        return ItemResult(item={key: str(out)}, outputs={"clips": str(out)})


def _image_for(params: dict[str, Any], index: int) -> object:
    """The ``image`` for this clip: the value itself, or the list entry by position."""
    image = params.get("image")
    if isinstance(image, list):
        return image[index] if index < len(image) else None
    return image


def _show_window(params: dict[str, Any], field: str = "overlay") -> tuple[float, float | None]:
    """The (start, end) seconds the overlay is visible; end None = whole clip."""
    show = params.get("show")
    if show is None:
        return 0.0, None
    if not isinstance(show, dict):
        raise ValueError(f"{field}: 'show' must be a mapping with 'from'/'to'")
    if "except" in show:
        raise ValueError(f"{field}: show.except is not supported yet (use show.from/show.to)")
    start = parse_seconds(show.get("from", 0))
    end = parse_seconds(show["to"]) if "to" in show else None
    if end is not None and end <= start:
        raise ValueError(f"{field}: show.to must be after show.from")
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


def _run_font(value: object, field: str) -> str:
    """Resolve a run's font to a family name safe to write into an ASS tag.

    ``\\fn`` takes the family verbatim up to the closing brace, so a name
    carrying a brace or a backslash would end the override block early and let
    the rest through as directives — the one hole the text escaping cannot
    cover, because this string is deliberately *not* escaped.
    """
    family = fonts.family(value)
    if any(ch in family for ch in "{}\\"):
        raise ValueError(f"{field}: font name '{family}' cannot contain {{, }} or \\")
    return family


def _ass_body(text: object, field: str = "overlay.text") -> str:
    """The ASS Dialogue text: a plain string, or ``{text, color, size, font}`` runs.

    Runs are what a mixed paragraph needs — the highlighted-keyword look where
    each phrase carries its own colour, and the list look where a big number is
    followed by a small label on the same line. The pipeline never writes ASS:
    it names a colour, a size and a font, and this function emits the override
    block. The run's own text still goes through :func:`escape_text`, so its
    braces and backslashes are neutralised and the tags below are the only ones
    libass can ever see.

    Runs are concatenated verbatim, spaces included — ``{text: "Apollo 11's"}``
    followed by ``{text: " flag"}`` is how the two stay apart.
    """
    if isinstance(text, str):
        body = _ass_lines(text, strip=True)
        if not body:
            raise ValueError(f"{field}: is empty")
        return body
    if not isinstance(text, list) or not text:
        raise ValueError(f"{field}: must be a string or a list of {{text, color}} runs")

    parts = []
    for index, run in enumerate(text):
        if not isinstance(run, dict) or not str(run.get("text", "")).strip():
            raise ValueError(f"{field} run {index} needs a non-empty 'text'")
        body = _ass_lines(str(run["text"]), strip=False)
        tags = ""
        if run.get("color"):
            # `\c` wants the &H..& override form; _ass_color returns the bare
            # Style-field form, so the closing & is added here.
            tags += f"\\c{_ass_color(run['color'], f'{field}[{index}].color')}&"
        if run.get("size") is not None:
            size = run["size"]
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ValueError(f"{field}[{index}].size must be a positive integer (px)")
            tags += f"\\fs{size}"
        if run.get("font"):
            tags += f"\\fn{_run_font(run['font'], f'{field}[{index}].font')}"
        # `\r` resets to the Style, so one run's overrides never leak into the next.
        parts.append(f"{{{tags}}}{body}{{\\r}}" if tags else body)
    return "".join(parts)


def _run_fonts(text: object) -> list[object]:
    """Every ``font`` named inside a run, so the presets can be fetched up front."""
    if not isinstance(text, list):
        return []
    return [run["font"] for run in text if isinstance(run, dict) and run.get("font")]


def _align(value: object, field: str, default: int) -> int:
    """Map a nine-anchor position name to its ASS alignment code."""
    if value is None:
        return default
    key = str(value).lower()
    if key not in TEXT_POSITIONS:
        valid = ", ".join(sorted(TEXT_POSITIONS))
        raise ValueError(f"{field}: unknown position '{value}' (choose from: {valid})")
    return TEXT_POSITIONS[key]


def _band_align(params: dict[str, Any]) -> int:
    """The alignment a `band` implies when no explicit `position` is given.

    Text has always followed its band to the top or the bottom of the frame;
    `position` only overrides that when the pipeline asks for it.
    """
    band = params.get("band")
    if not isinstance(band, dict):
        return TEXT_POSITIONS["top"]
    return TEXT_POSITIONS["top" if str(band.get("position", "top")).lower() == "top" else "bottom"]


def _anchor(
    align: int, margin_l: int, margin_r: int, margin_v: int, size_wh: tuple[int, int]
) -> tuple[float, float]:
    """The pixel the cue is pinned to, from its anchor and its margins.

    The point matches what the alignment already means: the text's top-left for
    ``top-left``, its bottom-centre for ``bottom-center``, and so on — so the
    same margins land in the same place whether the cue is pinned or flowed.
    """
    width, height = size_wh
    column, row = (align - 1) % 3, (align - 1) // 3
    x = (margin_l, width / 2, width - margin_r)[column]
    y = (height - margin_v, height / 2, margin_v)[row]
    return x, y


def _cue(
    params: dict[str, Any],
    cue: dict[str, Any],
    field: str,
    window: tuple[float, float | None],
    size_wh: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """One styled, timed line: cue values win, then the block's, then the defaults."""
    size = cue.get("size", params.get("size", _DEFAULT_SIZE))
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{field}: size must be a positive integer (px)")
    body = _ass_body(cue["text"], f"{field}.text")
    line_count = body.count("\\N") + 1

    band = params.get("band")
    if isinstance(band, dict):
        # Centre the text block vertically inside the band — as before, this
        # wins over `margin`, which the spec has always scoped to bandless text.
        margin_v = max((int(band.get("height", _DEFAULT_BAND_HEIGHT)) - size * line_count) // 2, 0)
    else:
        margin_v = int(cue.get("margin", params.get("margin", _DEFAULT_MARGIN)))

    start, end = window
    if isinstance(cue.get("show"), dict) or "show" in cue:
        start, end = _show_window(cue, field)
    margin_x = int(cue.get("margin_x", params.get("margin_x", _DEFAULT_MARGIN_X)))
    outline = cue.get("outline", params.get("outline", _DEFAULT_OUTLINE))
    if isinstance(outline, bool) or not isinstance(outline, (int, float)) or outline < 0:
        raise ValueError(f"{field}: outline must be a number of pixels >= 0")
    align = _align(
        cue.get("position", params.get("position")), f"{field}.position", _band_align(params)
    )
    return {
        "text": body,
        "start": start,
        "end": end,
        "font": _run_font(cue.get("font", params.get("font")), f"{field}.font"),
        "size": size,
        "primary": _ass_color(cue.get("color", params.get("color")), f"{field}.color"),
        "outline": _ass_color(
            cue.get("outline_color", params.get("outline_color", "black")),
            f"{field}.outline_color",
        ),
        "outline_w": float(outline),
        "align": align,
        "margin_l": margin_x,
        "margin_r": margin_x,
        "margin_v": margin_v,
        # Pinned only in `cues` mode, where several lines share the screen and
        # would otherwise be pushed apart by libass's collision avoidance. A
        # lone `text` keeps flowing from its margins exactly as it always has.
        "pos": _anchor(align, margin_x, margin_x, margin_v, size_wh) if size_wh else None,
    }


def _cue_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    """The block's cues: the `cues` list, or the single `text` as a one-cue list."""
    cues = params.get("cues")
    if cues is not None:
        if not isinstance(cues, list) or not cues:
            raise ValueError("overlay: 'cues' must be a non-empty list of {text, show} mappings")
        for index, cue in enumerate(cues):
            if not isinstance(cue, dict) or cue.get("text") is None:
                raise ValueError(f"overlay.cues[{index}]: needs a 'text'")
        return cues
    return [{"text": params["text"]}] if params.get("text") else []


def _text_ass(
    params: dict[str, Any],
    ctx: RunContext,
    name: str,
    size_wh: tuple[int, int],
    start: float,
    end: float | None,
) -> Path:
    """Write the ASS file for the overlay text: one Style + Dialogue per cue."""
    listed = bool(params.get("cues"))
    cues = [
        _cue(
            params,
            cue,
            f"overlay.cues[{i}]" if listed else "overlay",
            (start, end),
            size_wh if listed else None,
        )
        for i, cue in enumerate(_cue_list(params))
    ]
    path = ctx.work_dir() / f"{name}.ass"
    path.write_text(document(size_wh[0], size_wh[1], cues))
    return path


def _overlay(
    media: str,
    params: dict[str, Any],
    ctx: RunContext,
    name: str,
    out: Path,
    index: int = 0,
) -> None:
    image = _image_for(params, index)
    cues = _cue_list(params)
    if not cues and not image:
        raise ValueError("overlay: needs a 'text' (or 'cues') and/or an 'image'")
    start, end = _show_window(params)
    size_wh = ffmpeg.probe_resolution(media)

    band_chain: list[str] = []
    band = params.get("band")
    if band is not None:
        if not isinstance(band, dict):
            raise ValueError("overlay: 'band' must be a mapping (color/height/position)")
        band_chain.append(_band_filter(band, size_wh[1], start, end))

    text_chain: list[str] = []
    if cues:
        # Fetch every preset named anywhere — the block, a cue, or a run — before
        # libass looks for it, otherwise it silently substitutes another family.
        for font in [params.get("font"), *(c.get("font") for c in cues)]:
            fonts.ensure(font)
        for cue in cues:
            for font in _run_fonts(cue["text"]):
                fonts.ensure(font)
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
