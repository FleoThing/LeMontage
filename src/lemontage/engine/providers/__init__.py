"""Pluggable speech providers.

v1 ships a local-only STT provider: ``faster-whisper`` for transcription. The
base class defines the contract so cloud providers can be added later without
touching the blocks. (Text-to-speech is deferred to v2 — see TODO.)
"""

from __future__ import annotations

from .base import Segment, STTProvider, Transcript, Word

__all__ = ["STTProvider", "Segment", "Transcript", "Word"]


def default_stt(model: str = "base") -> STTProvider:
    """Return the default local STT provider (faster-whisper)."""
    from .whisper import WhisperSTT

    return WhisperSTT(model=model)
