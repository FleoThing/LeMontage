"""Provider contracts shared by every STT/TTS backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Segment:
    """A timed slice of transcribed speech."""

    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass
class Transcript:
    """The result of a transcription: full text, timed segments, language."""

    text: str
    segments: list[Segment]
    lang: str


@dataclass
class TTSResult:
    """The result of a synthesis: path to the written audio and its duration."""

    audio: Path
    duration: float


class STTProvider(ABC):
    """Transcribes an audio/video file into timed text."""

    @abstractmethod
    def transcribe(self, media: str | Path, lang: str = "auto", **options: object) -> Transcript:
        """Transcribe ``media``; ``lang`` is an ISO code or ``"auto"``.

        Backends may accept extra ``options`` (e.g. ``vad_filter``, ``beam_size``)
        and ignore any they do not support.
        """


class TTSProvider(ABC):
    """Synthesizes speech audio from text."""

    @abstractmethod
    def synthesize(
        self, text: str, out_path: str | Path, voice: str = "default", speed: float = 1.0
    ) -> TTSResult:
        """Write spoken ``text`` to ``out_path`` and return the result."""
