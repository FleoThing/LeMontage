"""Thin wrapper around the FFmpeg binary.

Prefers a system ``ffmpeg`` on ``PATH``; falls back to the static binary shipped
by ``imageio-ffmpeg`` so Reelflow works out of the box without a system install.
"""

from __future__ import annotations

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
        raise FFmpegError(
            "ffmpeg not found on PATH and imageio-ffmpeg is not installed"
        ) from exc
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
    proc = subprocess.run(
        [ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True
    )
    return _parse_duration(proc.stderr)


def _parse_duration(stderr: str) -> float:
    import re

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if not match:
        raise FFmpegError("could not determine media duration")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
