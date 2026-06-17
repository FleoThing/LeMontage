"""Local speech-to-text via ``faster-whisper``.

The model is downloaded and cached by ``faster-whisper`` itself (under
``~/.cache/huggingface``) on first use. The heavy import is deferred to
construction time so importing Reelflow stays cheap.
"""

from __future__ import annotations

from pathlib import Path

from .base import Segment, STTProvider, Transcript, Word

_VALID_SIZES = frozenset({"tiny", "base", "small", "medium", "large", "large-v3"})


class WhisperSTT(STTProvider):
    """``faster-whisper`` backend running on CPU (``int8``) by default."""

    def __init__(
        self,
        model: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        if model not in _VALID_SIZES:
            raise ValueError(
                f"unknown whisper model '{model}' (expected one of {sorted(_VALID_SIZES)})"
            )
        self._model_size = model
        self._device = device
        self._compute_type = compute_type
        self._model = None  # lazily constructed on first transcribe

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(
        self,
        media: str | Path,
        lang: str = "auto",
        *,
        vad_filter: bool = True,
        beam_size: int = 5,
        **_: object,
    ) -> Transcript:
        model = self._ensure_model()
        language = None if lang in (None, "", "auto") else lang
        # vad_filter drops non-speech (silence, crowd, music), which removes most
        # of Whisper's hallucinated text; a wider beam improves accuracy.
        # word_timestamps gives per-word timing for karaoke-style captions.
        raw_segments, info = model.transcribe(
            str(media),
            language=language,
            beam_size=int(beam_size),
            vad_filter=bool(vad_filter),
            word_timestamps=True,
        )

        segments = [
            Segment(
                start=round(float(s.start), 3),
                end=round(float(s.end), 3),
                text=s.text.strip(),
                words=[
                    Word(
                        start=round(float(w.start), 3),
                        end=round(float(w.end), 3),
                        text=w.word.strip(),
                    )
                    for w in (s.words or [])
                ],
            )
            for s in raw_segments
        ]
        text = " ".join(s.text for s in segments).strip()
        return Transcript(text=text, segments=segments, lang=info.language)
