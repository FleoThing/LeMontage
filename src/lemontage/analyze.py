"""``lemontage analyze`` — distil a video into a compact JSON manifest (a VSO).

The point: an AI agent editing video with LeMontage otherwise screenshots the
media frame by frame, burns tokens, and never really "watches" it. This command
reads the whole video *once* with cheap local analysis and emits a small JSON
manifest the agent reads instead — shots with per-shot loudness, speech words,
and dead-air spans — so it can pick good moments before touching the editor.

v1 is FFmpeg + faster-whisper only (no OpenCV): shots via scene detection,
per-shot loudness, dead-air via silencedetect, words via Whisper. Per-shot
visual quality (motion/sharpness) is a later phase behind an ``[analyze]`` extra.
"""

from __future__ import annotations

from .engine import ffmpeg
from .engine.blocks.detect_clips import (
    _loudness_timeline,
    _spans_from_scene_cuts,
    _speech_spans_from_silence,
)
from .engine.providers.whisper import WhisperSTT


def analyze_video(
    path: str, *, transcribe: bool = True, model: str = "base", lang: str = "auto"
) -> dict:
    """Analyse ``path`` and return the VSO manifest (see module docstring)."""
    path = str(path)
    duration = ffmpeg.probe_duration(path)
    audio = ffmpeg.has_audio(path)

    loud = _loudness_timeline(path) if audio else []
    shots = [
        {
            "id": i + 1,
            "start": round(start, 3),
            "end": round(end, 3),
            "loudness_db": _avg_loudness(loud, start, end),
        }
        for i, (start, end) in enumerate(_spans_from_scene_cuts(path, duration))
    ]

    manifest: dict = {
        "duration": round(duration, 3),
        "fps": ffmpeg.probe_fps(path),
        "has_audio": audio,
        "shots": shots,
    }

    if audio:
        speech: dict = {"dead_air": _dead_air(path, duration)}
        if transcribe:
            speech["words"] = _transcribe_words(path, model, lang)
        manifest["speech"] = speech

    return manifest


def _avg_loudness(timeline: list[tuple[float, float]], start: float, end: float) -> float | None:
    """Mean RMS level (dB) of the windows falling inside ``[start, end]``.

    ponytail: arithmetic mean of dB, not energy-summed — a coarse per-shot proxy,
    good enough for an agent ranking shots, not for mastering."""
    levels = [db for t, db in timeline if start <= t < end]
    return round(sum(levels) / len(levels), 1) if levels else None


def _dead_air(path: str, duration: float) -> list[list[float]]:
    """Silence spans = the gaps *between* the speech spans silencedetect leaves."""
    speech = _speech_spans_from_silence(path, duration)
    gaps: list[list[float]] = []
    cursor = 0.0
    for start, end in speech:
        if start > cursor:
            gaps.append([round(cursor, 3), round(start, 3)])
        cursor = end
    if cursor < duration:
        gaps.append([round(cursor, 3), round(duration, 3)])
    return gaps


def _transcribe_words(path: str, model: str, lang: str) -> list[dict]:
    """Flatten Whisper's segments to compact ``{t, d, w}`` word entries."""
    transcript = WhisperSTT(model=model).transcribe(path, lang=lang)
    return [
        {"t": round(w.start, 3), "d": round(w.end - w.start, 3), "w": w.text.strip()}
        for seg in transcript.segments
        for w in seg.words
    ]
