"""Parse the time formats the spec accepts into seconds.

* durations:  ``30s``, ``1m30s``, ``90`` (bare seconds)
* timecodes:  ``HH:MM:SS``, ``MM:SS``, ``75``, ``75.5``
"""

from __future__ import annotations

import re

_DURATION = re.compile(r"^(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$")


def parse_seconds(value: object) -> float:
    """Parse a duration *or* a timecode into seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"expected a time value, got {value!r}")

    text = value.strip()
    if ":" in text:
        return _parse_timecode(text)

    match = _DURATION.match(text)
    if not match or not any(match.groups()):
        raise ValueError(f"invalid duration: {value!r}")
    minutes, seconds = match.groups()
    total = 0.0
    if minutes:
        total += int(minutes) * 60
    if seconds:
        total += float(seconds)
    return total


def _parse_timecode(text: str) -> float:
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid timecode: {text!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def to_timecode(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` (for SRT and ffmpeg)."""
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
