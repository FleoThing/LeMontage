"""``export`` — render the final video(s) to disk (SPEC §6.6).

An optional ``title`` draws a persistent banner at the top of the frame for the
whole clip. It is rendered with libass (the static FFmpeg build ships no
``drawtext``), so it composes after the vertical scale+pad in the same way the
``captions`` block burns subtitles.

An optional ``author`` draws a small persistent credit label — the source
channel of the clip, or the editor's own handle. It defaults to the top-left
corner because the Shorts/TikTok player UI covers the right edge and the
bottom of the frame; centred positions are also available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg, fonts, safepath
from ..assformat import escape_text
from ..context import RunContext
from .base import Block, BlockResult, ItemResult

# Sanity bounds so a pipeline can't ask FFmpeg for an absurd allocation.
_MAX_DIMENSION = 7680  # 8K per side
_MAX_FPS = 240
_MAX_TITLE_SIZE = 2000

_RESOLUTIONS = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
    "square": (1080, 1080),
}

_DEFAULT_TITLE_SIZE = 34
_DEFAULT_TITLE_MARGIN = 120  # distance from the top edge (into the letterbox band)

_DEFAULT_AUTHOR_SIZE = 26
_DEFAULT_AUTHOR_MARGIN = 60  # distance from the frame edges

# ASS numpad alignments for the author label positions.
_AUTHOR_POSITIONS = {
    "top-left": 7,
    "top-center": 8,
    "top-right": 9,
    "bottom-left": 1,
    "bottom-center": 2,
    "bottom-right": 3,
}

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
    "Style: Title,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
    "-1,0,0,0,100,100,0,0,1,2,1,8,40,40,{margin},1\n"
    """\

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.99,Title,,0,0,0,,{text}
"""
)

# Slightly transparent white with a thin outline: readable on any footage
# without stealing attention from the captions or the title.
_AUTHOR_ASS_TEMPLATE = (
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
    "Style: Author,{font},{size},&H20FFFFFF,&H000000FF,&H00000000,&H96000000,"
    "0,0,0,0,100,100,0,0,1,1,0,{align},{margin},{margin},{margin},1\n"
    """\

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.99,Author,,0,0,0,,{text}
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
        author = _author_ass(params, ctx, f"{step_id}-author", index=0)
        _render(media, params, out, title, author)
        return BlockResult(outputs={"files": [str(out)]})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip")
        if clip is None:
            raise ValueError("export: channel item has no 'clip' to export")
        out = _output_path(params, ctx, index=item["index"])
        title = _title_ass(params, ctx, f"{step_id}-{item['index']}-title", index=item["index"])
        author = _author_ass(params, ctx, f"{step_id}-{item['index']}-author", index=item["index"])
        _render(clip, params, out, title, author)
        return ItemResult(item={"file": str(out)}, outputs={"files": str(out)})


def _target_size(params: dict[str, Any]) -> tuple[int, int]:
    if params.get("resolution"):
        raw = str(params["resolution"]).lower()
        try:
            width_s, height_s = raw.split("x")
            width, height = int(width_s), int(height_s)
        except ValueError as exc:
            raise ValueError(
                f"export: invalid resolution '{params['resolution']}' (expected WIDTHxHEIGHT)"
            ) from exc
        for dim in (width, height):
            if not 0 < dim <= _MAX_DIMENSION:
                raise ValueError(
                    f"export: resolution {width}x{height} out of range "
                    f"(each side must be 1..{_MAX_DIMENSION})"
                )
        return width, height
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
    # A pipeline-supplied path must not escape the output tree (path traversal).
    out = safepath.confine(out, safepath.allowed_roots(ctx.output_dir))
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
    if not title:
        return None
    width, height = _target_size(params)
    size = int(params.get("title_size", _DEFAULT_TITLE_SIZE))
    if not 0 < size <= _MAX_TITLE_SIZE:
        raise ValueError(f"export: title_size {size} out of range (1..{_MAX_TITLE_SIZE})")
    margin = int(params.get("title_margin", _DEFAULT_TITLE_MARGIN))
    font = fonts.family(params.get("title_font"))
    raw = _fill_title_tokens(str(title), index, ctx.pipeline_name)
    # Accept both literal "\n" and real newlines; ASS uses "\N" for a line break.
    # Escape each line's content before joining with our own "\N" directive, so
    # the title text can't inject ASS override blocks.
    lines = [escape_text(ln.strip()) for ln in raw.replace("\\n", "\n").splitlines() if ln.strip()]
    text = r"\N".join(lines)

    path = ctx.work_dir() / f"{name}.ass"
    path.write_text(
        _ASS_TEMPLATE.format(w=width, h=height, font=font, size=size, margin=margin, text=text)
    )
    return path


def _author_ass(params: dict[str, Any], ctx: RunContext, name: str, index: int = 0) -> Path | None:
    """Write an ASS file for the author/credit label, if requested.

    ``author`` credits the clip's source channel — or the editor's own handle
    when the footage is theirs. Same token support as the title
    (``{{ part }}``, ``{{ index }}``, ``{{ name }}``). ``author_position``
    picks a corner or a centred spot; the default ``top-left`` stays clear of
    the Shorts/TikTok player UI, which covers the right edge and the bottom of
    the frame. ``top-center`` sits in the title band (mind ``title``) and
    ``bottom-center`` in the captions band — bump ``author_margin`` if both
    are in play.
    """
    author = params.get("author")
    if not author:
        return None
    position = str(params.get("author_position", "top-left"))
    if position not in _AUTHOR_POSITIONS:
        valid = ", ".join(sorted(_AUTHOR_POSITIONS))
        raise ValueError(f"export: unknown author_position '{position}' (choose from: {valid})")
    width, height = _target_size(params)
    size = int(params.get("author_size", _DEFAULT_AUTHOR_SIZE))
    margin = int(params.get("author_margin", _DEFAULT_AUTHOR_MARGIN))
    font = fonts.family(params.get("author_font"))
    text = _fill_title_tokens(str(author), index, ctx.pipeline_name)

    path = ctx.work_dir() / f"{name}.ass"
    path.write_text(
        _AUTHOR_ASS_TEMPLATE.format(
            w=width,
            h=height,
            font=font,
            size=size,
            align=_AUTHOR_POSITIONS[position],
            margin=margin,
            text=text,
        )
    )
    return path


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


def _render(
    media: str,
    params: dict[str, Any],
    out: Path,
    title: Path | None = None,
    author: Path | None = None,
) -> None:
    width, height = _target_size(params)
    fps = int(params.get("fps", 30))
    if not 0 < fps <= _MAX_FPS:
        raise ValueError(f"export: fps {fps} out of range (1..{_MAX_FPS})")
    chain = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        f"fps={fps}",
    ]
    if title is not None:
        fonts.ensure(params.get("title_font"))  # download preset / warn on missing
        chain.append(fonts.libass_filter(title))
    if author is not None:
        fonts.ensure(params.get("author_font"))
        chain.append(fonts.libass_filter(author))
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
