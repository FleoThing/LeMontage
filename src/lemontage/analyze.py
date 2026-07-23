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
    path: str,
    *,
    transcribe: bool = True,
    visual: bool = False,
    model: str = "base",
    lang: str = "auto",
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
    if visual:
        _visual_scores(path, shots)

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


def _visual_scores(path: str, shots: list[dict], samples: int = 4) -> None:
    """Add per-shot ``sharpness`` and ``motion`` (0–1, 1 = best in this video).

    Both are relative *within* the video so an agent can rank shots: sharpness is
    the mean Laplacian variance of sampled frames (low = soft/blurry/text card),
    motion the mean dense optical-flow magnitude between them (low = static).
    Needs the ``[analyze]`` extra (OpenCV + NumPy). Mutates ``shots`` in place.

    ponytail: 4 frames/shot at 320px width — cheap; bump ``samples`` if a shot's
    single motion peak is being missed."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "visual analysis needs the [analyze] extra: pip install 'lemontage[analyze]'"
        ) from exc

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {path} for visual analysis")
    raw: list[tuple[float, float]] = []
    read = 0
    try:
        for shot in shots:
            frames = _sample_gray(cap, shot["start"], shot["end"], samples, cv2)
            read += len(frames)
            sharp = (
                float(np.mean([cv2.Laplacian(f, cv2.CV_64F).var() for f in frames]))
                if frames
                else 0.0
            )
            raw.append((sharp, _mean_flow(frames, cv2, np)))
    finally:
        cap.release()
    # OpenCV silently returns no frames for codecs it can't decode (notably AV1),
    # which would emit an all-zero, meaningless scoring. Fail loudly instead.
    if shots and read == 0:
        raise RuntimeError(
            f"visual analysis decoded no frames from {path} — the codec may be "
            "unsupported by OpenCV (e.g. AV1). Transcode to H.264 first."
        )
    _apply_normalized(shots, raw)


def _sample_gray(cap, start: float, end: float, n: int, cv2) -> list:
    """Grab ``n`` evenly-spaced grayscale frames from ``[start, end]``, ≤320px wide."""
    frames = []
    span = end - start
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + (i + 0.5) / n * span) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if gray.shape[1] > 320:
            gray = cv2.resize(gray, (320, round(320 * gray.shape[0] / gray.shape[1])))
        frames.append(gray)
    return frames


def _mean_flow(frames: list, cv2, np) -> float:
    """Mean dense optical-flow magnitude across consecutive frames (0 if <2)."""
    if len(frames) < 2:
        return 0.0
    mags = []
    for a, b in zip(frames, frames[1:], strict=False):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mags.append(float(np.mean(np.hypot(flow[..., 0], flow[..., 1]))))
    return sum(mags) / len(mags)


def _apply_normalized(shots: list[dict], raw: list[tuple[float, float]]) -> None:
    """Scale raw (sharpness, motion) by the video's max so the best shot = 1.0."""
    max_s = max((s for s, _ in raw), default=0.0) or 1.0
    max_m = max((m for _, m in raw), default=0.0) or 1.0
    for shot, (sharp, motion) in zip(shots, raw, strict=False):
        shot["sharpness"] = round(sharp / max_s, 3)
        shot["motion"] = round(motion / max_m, 3)


def _transcribe_words(path: str, model: str, lang: str) -> list[dict]:
    """Flatten Whisper's segments to compact ``{t, d, w}`` word entries."""
    transcript = WhisperSTT(model=model).transcribe(path, lang=lang)
    return [
        {"t": round(w.start, 3), "d": round(w.end - w.start, 3), "w": w.text.strip()}
        for seg in transcript.segments
        for w in seg.words
    ]
