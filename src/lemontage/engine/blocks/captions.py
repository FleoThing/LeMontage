"""``captions`` — render subtitles onto a video (SPEC §6.5).

With per-word timing (``words``, from ``stt``) it burns **karaoke** captions:
short lines where each word lights up exactly when it's spoken — the TikTok /
CapCut look. Without word timing it falls back to segment-level SRT cues.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg, fonts
from ..assformat import escape_text
from ..context import RunContext
from ..timecode import to_timecode
from .base import Block, BlockResult, ItemResult
from .export import _output_path

# ASS Alignment is numpad-style: 2=bottom-centre, 5=middle, 8=top.
_POSITION = {"bottom": 2, "center": 5, "top": 8}

# Per-style (outline weight, bold). Colours/size are handled by the karaoke path.
_STYLES = {
    "default": (1, 0),
    "tiktok": (3, -1),
    "minimal": (1, 0),
}

_DEFAULT_MAX_CHARS = 24  # short lines read better word-by-word
_DEFAULT_MARGIN_H = 80  # left/right margins when the whole width is usable
_MAX_WORDS_PER_LINE = 5
_LINE_GAP = 1.2  # start a new line after a silence longer than this (seconds)
_DEFAULT_POP = 115  # active-word scale (%) when `pop: true`
_POP_MS = 90  # how long the pop takes to settle back to 100%
# ASS colours are &HAABBGGRR. Default: spoken word yellow, upcoming word white.
_HIGHLIGHT = "&H0000FFFF"
_BASE = "&H00FFFFFF"

_KARAOKE_ASS = (
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
    "Style: Cap,{font},{size},{hi},{base},&H00000000,&H64000000,{bold},0,0,0,"
    "100,100,0,0,1,{outline},1,{align},{marginh},{marginh},{marginv},1\n"
    """\

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""
)


class CaptionsBlock(Block):
    name = "captions"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("captions: no input media")
        out = self._caption(media, params, ctx, step_id, offset=0.0)
        key = "srt" if not params.get("burn", True) else "clips"
        return BlockResult(outputs={key: str(out)}) if out else BlockResult(outputs={})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        # Prefer the exported file: placing `captions` *after* `export` burns the
        # captions on the already-reframed (e.g. vertical) clip, so their size is
        # relative to the final frame instead of being shrunk by the reframe.
        # Otherwise fall back to the cut clip (the classic cut→captions→export).
        key = "file" if item.get("file") else "clip"
        media = item.get("file") or item.get("clip")
        if media is None:
            raise ValueError(
                "captions: channel item has no 'clip'/'file' (run 'cut' or 'export' first)"
            )
        offset = float(item.get("start", 0.0))
        dest = _output_path(params, ctx, item["index"]) if params.get("output") else None
        out = self._caption(media, params, ctx, f"{step_id}-{item['index']}", offset, dest)
        if out is None:  # nothing in this clip's window — leave it unchanged
            return ItemResult(item={key: str(media)}, outputs={"clips": str(media)})
        if not params.get("burn", True):
            return ItemResult(item={"srt": str(out)}, outputs={"srt": str(out)})
        return ItemResult(item={key: str(out)}, outputs={"clips": str(out)})

    def _caption(self, media, params, ctx, name, offset, dest: Path | None = None) -> Path | None:
        lines = _build_lines(params, offset)
        if not lines:
            return None
        if not params.get("burn", True):
            return _write_srt(lines, ctx.work_dir() / f"{name}.srt")
        ass = _write_karaoke_ass(lines, params, media, ctx.work_dir() / f"{name}.ass")
        out = dest or ctx.work_dir() / f"{name}-captioned.mp4"
        _burn(media, ass, params, out)
        return out


def _build_lines(params: dict[str, Any], offset: float) -> list[dict[str, Any]]:
    """Group words (preferred) or segments into short, clip-local caption lines."""
    words = params.get("words")
    if isinstance(words, list) and words:
        max_chars = int(params.get("max_chars", _DEFAULT_MAX_CHARS))
        lines = _lines_from_words(words, offset, max_chars)
    else:
        segments = params.get("segments")
        if not isinstance(segments, list):
            raise ValueError("captions: provide 'words' (from stt) or 'segments'")
        lines = _lines_from_segments(segments, offset)
    return _uppercased(lines) if params.get("uppercase") else lines


def _uppercased(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upper-case every caption line (and its words).

    Done on the text, not with a renderer flag: the sidecar `.srt` gets the same
    treatment, and `max_chars` still counts the characters that are drawn.
    """
    for line in lines:
        line["text"] = line["text"].upper()
        for word in line["words"]:
            word["text"] = word["text"].upper()
    return lines


def _lines_from_words(
    words: list[dict[str, Any]], offset: float, max_chars: int
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    length = 0
    for w in words:
        start = float(w["start"]) - offset
        end = float(w["end"]) - offset
        if end <= 0:  # entirely before this clip
            continue
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        word = {"start": max(0.0, start), "end": end, "text": text}
        too_long = length + len(text) + 1 > max_chars or len(current) >= _MAX_WORDS_PER_LINE
        big_gap = current and word["start"] - current[-1]["end"] > _LINE_GAP
        if current and (too_long or big_gap):
            lines.append(_line(current))
            current, length = [], 0
        current.append(word)
        length += len(text) + 1
    if current:
        lines.append(_line(current))
    return lines


def _lines_from_segments(segments: list[dict[str, Any]], offset: float) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for seg in segments:
        start = float(seg["start"]) - offset
        end = float(seg["end"]) - offset
        if end <= 0:
            continue
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append({"start": max(0.0, start), "end": end, "words": [], "text": text})
    return lines


def _line(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
        "text": " ".join(w["text"] for w in words),
    }


def _safe_margin_h(params: dict[str, Any], width: int, height: int) -> int:
    """Horizontal margins that keep caption lines inside the centre 9:16 column.

    Captions are usually burned onto the landscape source and only exported to
    vertical afterwards — where ``fit: cover`` keeps just the centre 9:16 crop,
    so any text wider than that column ends up off-frame. ``safe_area: false``
    restores the full-width margins (e.g. for a horizontal final export).
    """
    if width <= height or not params.get("safe_area", True):
        return _DEFAULT_MARGIN_H
    safe = height * 9 // 16
    padding = round(safe * 0.05)
    return max(_DEFAULT_MARGIN_H, (width - safe) // 2 + padding)


def _write_karaoke_ass(lines, params, media, path: Path) -> Path:
    width, height = ffmpeg.probe_resolution(media)
    outline, bold = _STYLES.get(params.get("style", "tiktok"), _STYLES["tiktok"])
    size = int(params.get("caption_size") or 100)
    align = _POSITION.get(params.get("position", "bottom"), 2)
    marginv = int(params.get("caption_margin", round(height * 0.05)))
    marginh = _safe_margin_h(params, width, height)
    hi = params.get("highlight") or _HIGHLIGHT
    font = fonts.family(params.get("font"))

    events = "\n".join(
        event for line in lines for event in _events(line, params, hi=hi, base=_BASE)
    )
    path.write_text(
        _KARAOKE_ASS.format(
            w=width,
            h=height,
            font=font,
            size=size,
            hi=hi,
            base=_BASE,
            bold=bold,
            outline=outline,
            align=align,
            marginh=marginh,
            marginv=marginv,
            events=events,
        ),
        encoding="utf-8",
    )
    return path


def _events(line: dict[str, Any], params: dict[str, Any], hi: str, base: str) -> list[str]:
    """The ASS events for one caption line.

    Normally one karaoke event (`\\k` recolours each word as it is spoken). With
    `pop`, the line is emitted once **per word** instead: `\\k` can only change
    colour, and the word has to *scale* — the beat that makes short-form captions
    read as spoken rather than displayed. Each event draws the whole line, so the
    wrapping never changes; only the active word is coloured and briefly enlarged.
    """
    pop = _pop_scale(params)
    if pop is None or not line["words"]:
        return [_dialogue(line)]
    events = []
    words = line["words"]
    for i, w in enumerate(words):
        start = w["start"] if i else line["start"]
        end = words[i + 1]["start"] if i + 1 < len(words) else line["end"]
        if end <= start:
            continue
        text = " ".join(
            _styled(word["text"], hi if j == i else base, pop if j == i else None)
            for j, word in enumerate(words)
        )
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
    return events or [_dialogue(line)]


def _styled(text: str, colour: str, pop: int | None) -> str:
    """One word with its colour, and — when it is the active one — a scale pop.

    Every word carries an explicit colour tag rather than relying on `\\r`: the
    style's primary colour *is* the highlight (the karaoke path needs that), so a
    reset would leave the rest of the line highlighted.
    """
    if pop is None:
        return f"{{\\c{colour}\\fscx100\\fscy100}}{escape_text(text)}"
    return (
        f"{{\\c{colour}\\fscx{pop}\\fscy{pop}"
        f"\\t(0,{_POP_MS},\\fscx100\\fscy100)}}{escape_text(text)}"
    )


def _pop_scale(params: dict[str, Any]) -> int | None:
    """The active-word scale in percent, or None when `pop` is off."""
    pop = params.get("pop")
    if pop is None or pop is False:
        return None
    if pop is True:
        return _DEFAULT_POP
    return int(pop)


def _dialogue(line: dict[str, Any]) -> str:
    start, end = _ass_time(line["start"]), _ass_time(line["end"])
    words = line["words"]
    if not words:  # segment fallback: plain text, no karaoke
        return f"Dialogue: 0,{start},{end},Cap,,0,0,0,,{escape_text(line['text'])}"
    parts = []
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"]
        cs = max(1, round((nxt - w["start"]) * 100))  # absorb the gap to the next word
        # Escape the word text, then wrap it with our own karaoke tag: user text
        # (from the transcript) can never inject an ASS override block.
        parts.append(f"{{\\k{cs}}}{escape_text(w['text'])} ")
    return f"Dialogue: 0,{start},{end},Cap,,0,0,0,,{''.join(parts).rstrip()}"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _write_srt(lines: list[dict[str, Any]], path: Path) -> Path:
    out: list[str] = []
    for i, line in enumerate(lines, start=1):
        out.append(str(i))
        out.append(f"{_srt_ts(line['start'])} --> {_srt_ts(line['end'])}")
        out.append(line["text"])
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def _srt_ts(seconds: float) -> str:
    return to_timecode(max(0.0, seconds)).replace(".", ",")


def _burn(media: str, ass: Path, params: dict[str, Any], out: Path) -> None:
    fonts.ensure(params.get("font"))
    ffmpeg.run(["-i", str(media), "-vf", fonts.libass_filter(ass), "-c:a", "copy", str(out)])
