"""``export`` — render the final video(s) to disk (SPEC §6.6).

An optional ``title`` draws a persistent banner at the top of the frame for the
whole clip. It is rendered with libass (the static FFmpeg build ships no
``drawtext``), so it composes after the vertical scale+pad in the same way the
``captions`` block burns subtitles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...spec import EXPORT_FIT_MODES
from .. import ffmpeg, fonts
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult

_RESOLUTIONS = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
    "square": (1080, 1080),
}

_DEFAULT_TITLE_SIZE = 92
_DEFAULT_TITLE_MARGIN = 120  # distance from the top edge (into the letterbox band)

# Alignment 8 = top-centre. PlayResX/Y match the export size so FontSize is in
# real pixels; ScaledBorderAndShadow keeps the outline proportional.
_ASS_TEMPLATE = (
    """\
[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    "Style: Title,{font},{size},{primary},&H000000FF,&H00000000,&H64000000,"
    "-1,0,0,0,100,100,0,0,1,2,1,8,40,40,{margin},1\n"
    """\

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{start},{end},Title,,0,0,0,,{text}
"""
)


class ExportBlock(Block):
    name = "export"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("export: no input media")
        out = _output_path(params, ctx, index=0)
        title = _title_ass(params, ctx, f"{step_id}-title", index=0)
        _render(media, params, out, title, mute=_muted(params, 0))
        return BlockResult(outputs={"files": [str(out)]})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip")
        if clip is None:
            raise ValueError("export: channel item has no 'clip' to export")
        out = _output_path(params, ctx, index=item["index"])
        title = _title_ass(params, ctx, f"{step_id}-{item['index']}-title", index=item["index"])
        _render(clip, params, out, title, mute=_muted(params, item["index"]))
        return ItemResult(item={"file": str(out)}, outputs={"files": str(out)})


def _target_size(params: dict[str, Any]) -> tuple[int, int]:
    if params.get("resolution"):
        width, height = str(params["resolution"]).lower().split("x")
        return int(width), int(height)
    fmt = params.get("format", "vertical")
    if fmt not in _RESOLUTIONS:
        raise ValueError(f"export: unknown format '{fmt}'")
    return _RESOLUTIONS[fmt]


def _output_path(params: dict[str, Any], ctx: RunContext, index: int) -> Path:
    template = params.get("output")
    if template:
        # Same tokens as the title ({{ part }} / {{ index }} / {{ name }}). Without
        # {{ part }} support, a `{{ name }}-{{ part }}.mp4` template left the literal
        # braces in the path, so every mapped clip wrote to the SAME file in
        # parallel and corrupted it (then concat failed reading it).
        out = Path(_fill_title_tokens(str(template), index, ctx.pipeline_name))
    else:
        out = ctx.output_dir / f"{ctx.pipeline_name}-{index}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _title_ass(params: dict[str, Any], ctx: RunContext, name: str, index: int = 0) -> Path | None:
    """Write an ASS file for the title, if requested.

    PlayResX/Y are set to the export resolution so ``title_size`` is in real
    pixels of the final frame (an SRT would size relative to libass's 384x288
    default and render far too large). The title text supports ``{{ part }}``
    (1-based clip number), ``{{ index }}`` (0-based) and ``{{ name }}``.
    """
    title = params.get("title")
    if not title or not _title_on_clip(params, index):
        return None
    width, height = _target_size(params)
    size = int(params.get("title_size", _DEFAULT_TITLE_SIZE))
    margin = int(params.get("title_margin", _DEFAULT_TITLE_MARGIN))
    font = fonts.family(params.get("title_font"))
    raw = _fill_title_tokens(str(title), index, ctx.pipeline_name)
    # Accept both literal "\n" and real newlines; ASS uses "\N" for a line break.
    lines = [ln.strip() for ln in raw.replace("\\n", "\n").splitlines() if ln.strip()]
    text = _fade_tag(params, index) + r"\N".join(lines)
    start, end = _title_window(params)

    path = ctx.work_dir() / f"{name}.ass"
    color = params.get("title_color")
    if isinstance(color, list):  # per-clip colour by position, like title_fade
        color = color[index] if index < len(color) else None
    primary = _ass_color(color)
    path.write_text(
        _ASS_TEMPLATE.format(
            w=width,
            h=height,
            font=font,
            size=size,
            margin=margin,
            text=text,
            start=start,
            end=end,
            primary=primary,
        )
    )
    return path


# A few named colours; anything else must be a #RRGGBB hex.
_NAMED_COLORS = {
    "white": "ffffff",
    "black": "000000",
    "red": "ff0000",
    "green": "00ff00",
    "blue": "0000ff",
    "yellow": "ffff00",
    "cyan": "00ffff",
    "magenta": "ff00ff",
    "orange": "ffa500",
    "pink": "ffc0cb",
    "gray": "808080",
    "grey": "808080",
}


def _ass_color(value: object) -> str:
    """Convert a ``#RRGGBB`` hex or a colour name to an ASS ``&H00BBGGRR`` string.

    ASS stores the primary colour as ``&HAABBGGRR`` (alpha, then blue/green/red),
    so we reorder the RGB bytes. Defaults to white when unset.
    """
    if not value:
        return "&H00FFFFFF"
    text = _NAMED_COLORS.get(str(value).strip().lower(), str(value).strip().lstrip("#").lower())
    if len(text) != 6 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"export: invalid title_color '{value}' (use #RRGGBB or a colour name)")
    rr, gg, bb = text[0:2], text[2:4], text[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def _fade_tag(params: dict[str, Any], index: int) -> str:
    """ASS ``\\fad`` override to fade the title in/out, or "" when not requested.

    ``title_fade`` (a duration) fades the title in at the start of its window and
    out at the end by that much — so it never pops on/off. A list applies a fade
    per clip by position (``[0, 0, 0.4]`` = fade only the 3rd clip), so titled
    clips can differ.
    """
    fade = params.get("title_fade")
    if isinstance(fade, list):
        fade = fade[index] if index < len(fade) else 0
    if not fade:
        return ""
    ms = int(round(parse_seconds(fade) * 1000))
    return rf"{{\fad({ms},{ms})}}"


def _title_on_clip(params: dict[str, Any], index: int) -> bool:
    """Whether this clip (0-based ``index``) should get the title.

    ``title_clips`` restricts the title to specific clips — an int or a list of
    0-based indices (e.g. ``[0]`` = only the first clip). Omit for every clip.
    """
    which = params.get("title_clips")
    if which is None:
        return True
    if isinstance(which, int) and not isinstance(which, bool):
        which = [which]
    return index in which


# Shown for the whole clip unless a window is given (libass clamps to the clip).
_TITLE_FOREVER = "9:59:59.99"


def _title_window(params: dict[str, Any]) -> tuple[str, str]:
    """Return the (start, end) ASS timestamps the title is visible for.

    ``title_start`` sets when it appears (default 0); ``title_end`` sets when it
    disappears, or ``title_duration`` gives that end as start + duration. With
    neither, the title stays for the whole clip.
    """
    start = parse_seconds(params.get("title_start", 0))
    if "title_end" in params:
        end = parse_seconds(params["title_end"])
    elif "title_duration" in params:
        end = start + parse_seconds(params["title_duration"])
    else:
        return _ass_timestamp(start), _TITLE_FOREVER
    if end <= start:
        raise ValueError("export: title window end must be after title_start")
    return _ass_timestamp(start), _ass_timestamp(end)


def _ass_timestamp(seconds: float) -> str:
    """Format seconds as an ASS timestamp ``H:MM:SS.cc`` (centiseconds)."""
    cs = int(round(seconds * 100))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _fill_title_tokens(text: str, index: int, name: str) -> str:
    for token, value in (
        ("{{ part }}", str(index + 1)),
        ("{{part}}", str(index + 1)),
        ("{{ index }}", str(index)),
        ("{{index}}", str(index)),
        ("{{ name }}", name),
        ("{{name}}", name),
    ):
        text = text.replace(token, value)
    return text


def _scale_chain(
    params: dict[str, Any], width: int, height: int, source_crop: str | None = None
) -> list[str]:
    """Video filters that fit the source into width×height per the `fit` mode.

    * ``contain`` (default) — scale to fit, then letterbox with black bars so the
      whole frame is visible.
    * ``cover`` — scale to fill, then centre-crop the overflow so there are no
      bars (the source edges are cropped instead).

    ``source_crop`` (a ``"w:h:x:y"`` spec) strips baked-in bars from the source
    *before* fitting, so a letterboxed source still fills the whole frame.
    """
    fit = str(params.get("fit", "contain")).lower()
    if fit not in EXPORT_FIT_MODES:
        valid = ", ".join(sorted(EXPORT_FIT_MODES))
        raise ValueError(f"export: unknown fit '{fit}' (choose from: {valid})")
    chain = [f"crop={source_crop}"] if source_crop else []
    if fit == "cover":
        chain += [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]
    else:  # contain
        chain += [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        ]
    return chain


def _muted(params: dict[str, Any], index: int) -> bool:
    """Whether this clip's audio should be silenced.

    ``mute: true`` silences every clip; a list silences per clip by position
    (``mute: [false, true, ...]``), so a single clip can be muted.
    """
    mute = params.get("mute")
    if isinstance(mute, list):
        return bool(mute[index]) if index < len(mute) else False
    return bool(mute)


def _render(
    media: str,
    params: dict[str, Any],
    out: Path,
    title: Path | None = None,
    mute: bool = False,
) -> None:
    width, height = _target_size(params)
    fps = int(params.get("fps", 30))
    # For `cover`, strip the source's own letterbox bars first (default on) so a
    # letterboxed source still fills the whole frame rather than carrying its bars
    # into the crop. Detected with FFmpeg's cropdetect — no extra dependency.
    source_crop = None
    if str(params.get("fit", "contain")).lower() == "cover" and params.get("trim_bars", True):
        source_crop = ffmpeg.detect_content_crop(media)
    chain = [*_scale_chain(params, width, height, source_crop), f"fps={fps}"]
    if title is not None:
        fonts.ensure(params.get("title_font"))  # download preset / warn on missing
        chain.append(fonts.libass_filter(title))
    args = ["-i", str(media), "-vf", ",".join(chain)]
    # Keep a (silent) audio stream rather than dropping it (-an), so a later
    # concat / crossfade still finds audio on every clip.
    if mute:
        args += ["-af", "volume=0"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out)]
    ffmpeg.run(args)
