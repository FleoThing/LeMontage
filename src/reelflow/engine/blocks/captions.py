"""``captions`` — render subtitles onto a video, or write a sidecar (SPEC §6.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import to_timecode
from .base import Block, BlockResult, ItemResult

# ASS Alignment is numpad-style: 2=bottom-centre, 5=middle, 8=top.
_POSITION = {"bottom": 2, "center": 5, "top": 8}

# MarginV / MarginL / MarginR keep the text inside a band instead of edge-to-edge.
_STYLES = {
    "default": "FontName=Arial,FontSize=16,Outline=1,Shadow=0,MarginV=40,MarginL=60,MarginR=60",
    "tiktok": (
        "FontName=Arial,FontSize=20,Bold=1,Outline=2,Shadow=1,"
        "PrimaryColour=&H00FFFFFF,MarginV=60,MarginL=80,MarginR=80"
    ),
    "minimal": "FontName=Arial,FontSize=14,Outline=0,MarginV=30,MarginL=60,MarginR=60",
}

# Default max characters per subtitle cue; longer segments are split so the text
# never fills the frame. Overridable via the captions `max_chars` param.
_DEFAULT_MAX_CHARS = 42


class CaptionsBlock(Block):
    name = "captions"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("captions: no input media")
        segments = _segments(params)
        max_chars = int(params.get("max_chars", _DEFAULT_MAX_CHARS))
        srt, count = _write_srt(segments, 0.0, ctx.work_dir() / f"{step_id}.srt", max_chars)
        if not params.get("burn", True):
            return BlockResult(outputs={"srt": str(srt)})
        if count == 0:  # nothing to caption — pass the media through unchanged
            return BlockResult(outputs={"clips": str(media)})
        out = ctx.work_dir() / f"{step_id}-captioned.mp4"
        _burn(media, srt, params, out)
        return BlockResult(outputs={"clips": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        clip = item.get("clip")
        if clip is None:
            raise ValueError("captions: channel item has no 'clip' (run 'cut' first)")
        # Shift segments into the clip's local timeline (clip starts at 0).
        offset = float(item.get("start", 0.0))
        segments = _segments(params)
        max_chars = int(params.get("max_chars", _DEFAULT_MAX_CHARS))
        srt, count = _write_srt(
            segments, offset, ctx.work_dir() / f"{step_id}-{item['index']}.srt", max_chars
        )

        if not params.get("burn", True):
            return ItemResult(item={"srt": str(srt)}, outputs={"srt": str(srt)})
        if count == 0:  # nothing in this clip's window — leave it unchanged
            return ItemResult(item={"clip": str(clip)}, outputs={"clips": str(clip)})

        out = ctx.work_dir() / f"{step_id}-{item['index']}-captioned.mp4"
        _burn(clip, srt, params, out)
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _segments(params: dict[str, Any]) -> list[dict[str, Any]]:
    segments = params.get("segments")
    if not isinstance(segments, list):
        raise ValueError("captions: 'segments' must be a list of {start, end, text}")
    return segments


def _write_srt(
    segments: list[dict[str, Any]], offset: float, path: Path, max_chars: int
) -> tuple[Path, int]:
    """Write an SRT shifted into the clip's local timeline. Returns (path, count).

    Long segments are split into short cues (``max_chars`` each) so the burned
    text never fills the frame.
    """
    lines: list[str] = []
    counter = 1
    for seg in segments:
        for cue in _split_cue(seg, max_chars):
            start = cue["start"] - offset
            end = cue["end"] - offset
            if end <= 0:  # entirely before this clip
                continue
            start = max(0.0, start)
            lines.append(str(counter))
            lines.append(f"{_srt_ts(start)} --> {_srt_ts(end)}")
            lines.append(cue["text"])
            lines.append("")
            counter += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, counter - 1


def _split_cue(seg: dict[str, Any], max_chars: int) -> list[dict[str, Any]]:
    """Split one segment into short cues, sharing its time span by text length."""
    text = str(seg.get("text", "")).strip()
    if not text:
        return []
    chunks = _pack_words(text, max_chars)
    start, end = float(seg["start"]), float(seg["end"])
    span = max(0.0, end - start)
    total = sum(len(c) for c in chunks) or 1

    cues: list[dict[str, Any]] = []
    cursor = start
    for chunk in chunks:
        dur = span * (len(chunk) / total)
        cues.append({"start": cursor, "end": cursor + dur, "text": chunk})
        cursor += dur
    cues[-1]["end"] = end  # avoid float drift on the last cue
    return cues


def _pack_words(text: str, max_chars: int) -> list[str]:
    """Greedily pack words into lines of at most ``max_chars`` characters."""
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks or [text]


def _srt_ts(seconds: float) -> str:
    return to_timecode(max(0.0, seconds)).replace(".", ",")


def _burn(media: str, srt: Path, params: dict[str, Any], out: Path) -> None:
    style = _STYLES.get(params.get("style", "default"), _STYLES["default"])
    alignment = _POSITION.get(params.get("position", "bottom"), 2)
    force_style = f"{style},Alignment={alignment}"
    # Escape the path for ffmpeg's filter parser (colons, backslashes).
    escaped = str(srt).replace("\\", "\\\\").replace(":", "\\:")
    vf = f"subtitles='{escaped}':force_style='{force_style}'"
    ffmpeg.run(["-i", str(media), "-vf", vf, "-c:a", "copy", str(out)])
