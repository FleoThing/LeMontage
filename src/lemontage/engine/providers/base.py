"""Provider contracts for speech backends (STT). TTS is deferred to v2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    """A single word with its own start/end (for karaoke-style captions)."""

    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass
class Segment:
    """A timed slice of transcribed speech, optionally with per-word timing."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [w.as_dict() for w in self.words],
        }


@dataclass
class Transcript:
    """The result of a transcription: full text, timed segments, language."""

    text: str
    segments: list[Segment]
    lang: str


class STTProvider(ABC):
    """Transcribes an audio/video file into timed text."""

    @abstractmethod
    def transcribe(self, media: str | Path, lang: str = "auto", **options: object) -> Transcript:
        """Transcribe ``media``; ``lang`` is an ISO code or ``"auto"``.

        Backends may accept extra ``options`` (e.g. ``vad_filter``, ``beam_size``)
        and ignore any they do not support.
        """
