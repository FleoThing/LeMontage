"""Local text-to-speech via ``kokoro-onnx``.

Model weights and the voice pack are fetched to ``~/.reelflow/models/`` on first
use (see :mod:`.models`). Synthesis runs on CPU through ONNX Runtime.
"""

from __future__ import annotations

from pathlib import Path

from .base import TTSProvider, TTSResult

# kokoro names voices like ``af_sarah``; ``default`` maps to a sensible English one.
_DEFAULT_VOICE = "af_sarah"


class KokoroTTS(TTSProvider):
    """``kokoro-onnx`` backend."""

    def __init__(self, lang: str = "en-us") -> None:
        self._lang = lang
        self._kokoro = None

    def _ensure_engine(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            from .models import ensure_kokoro

            model_path, voices_path = ensure_kokoro()
            self._kokoro = Kokoro(str(model_path), str(voices_path))
        return self._kokoro

    def synthesize(
        self, text: str, out_path: str | Path, voice: str = "default", speed: float = 1.0
    ) -> TTSResult:
        import soundfile as sf

        kokoro = self._ensure_engine()
        voice_name = _DEFAULT_VOICE if voice in (None, "", "default") else voice
        samples, sample_rate = kokoro.create(
            text, voice=voice_name, speed=float(speed), lang=self._lang
        )

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), samples, sample_rate)
        duration = round(len(samples) / float(sample_rate), 3)
        return TTSResult(audio=out, duration=duration)
