"""Thin wrapper around the FFmpeg binary.

Prefers a system ``ffmpeg`` on ``PATH``; falls back to the static binary shipped
by ``imageio-ffmpeg`` so LeMontage works out of the box without a system install.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg/FFprobe invocation fails."""


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """Locate an ffmpeg executable, preferring a system install."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - exercised only without deps
        raise FFmpegError("ffmpeg not found on PATH and imageio-ffmpeg is not installed") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str]) -> None:
    """Run ``ffmpeg <args>``, raising :class:`FFmpegError` on failure."""
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")


def run_capture(args: list[str]) -> str:
    """Run ``ffmpeg <args>`` and return its stderr (for filters that report there).

    Analysis filters like ``silencedetect`` and ``showinfo`` write their findings
    to stderr at the ``info`` log level, so we raise the verbosity here.
    """
    cmd = [ffmpeg_bin(), "-loglevel", "info", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stderr


def probe_duration(path: str | Path) -> float:
    """Return the duration of a media file in seconds, via ffprobe/ffmpeg."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    # Fallback: parse ffmpeg's stderr (no ffprobe in imageio-ffmpeg).
    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True)
    return _parse_duration(proc.stderr)


def probe_resolution(path: str | Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream."""
    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True)
    match = re.search(r",\s*(\d{2,5})x(\d{2,5})", proc.stderr)
    if not match:
        raise FFmpegError("could not determine video resolution")
    return int(match.group(1)), int(match.group(2))


def has_audio(path: str | Path) -> bool:
    """Return True if the media has at least one audio stream.

    Used by ``concat`` to stay tolerant of video-only clips (e.g. rendered
    stills carry no audio) — it drops the audio crossfade when a clip is silent.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        return bool(proc.stdout.strip())
    # No ffprobe (imageio-ffmpeg ships only ffmpeg): parse ffmpeg's stream dump.
    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True)
    return "Audio:" in proc.stderr


def detect_content_crop(path: str | Path) -> str | None:
    """Detect a video's non-black content rectangle via ``cropdetect``.

    Returns an ffmpeg crop spec ``"w:h:x:y"`` for stripping baked-in letterbox /
    pillar bars, or ``None`` when detection is inconclusive or there is nothing
    to crop. Uses only FFmpeg — no extra dependency.
    """
    proc = subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            "cropdetect=round=2",
            "-frames:v",
            "120",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
    if not matches:
        return None
    w, h, x, y = matches[-1]
    return f"{w}:{h}:{x}:{y}"


def _parse_duration(stderr: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if not match:
        raise FFmpegError("could not determine media duration")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
