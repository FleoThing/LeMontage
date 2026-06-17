"""Pluggable speech providers (STT/TTS).

v1 ships local-only providers: ``faster-whisper`` for transcription and
``kokoro-onnx`` for synthesis. The base classes define the contract so cloud
providers can be added later without touching the blocks.
"""

from __future__ import annotations

from .base import STTProvider, Segment, Transcript, TTSProvider, TTSResult

__all__ = ["STTProvider", "TTSProvider", "Segment", "Transcript", "TTSResult"]


def default_stt(model: str = "base") -> STTProvider:
    """Return the default local STT provider (faster-whisper)."""
    from .whisper import WhisperSTT

    return WhisperSTT(model=model)


def default_tts() -> TTSProvider:
    """Return the default local TTS provider (kokoro-onnx)."""
    from .kokoro import KokoroTTS

    return KokoroTTS()
