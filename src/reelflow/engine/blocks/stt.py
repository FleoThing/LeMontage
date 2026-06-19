"""``stt`` — transcribe the input media to timed text (SPEC §6.1)."""

from __future__ import annotations

from typing import Any

from .. import providers
from ..context import RunContext
from .base import Block, BlockResult


class SttBlock(Block):
    name = "stt"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if not media:
            raise ValueError("stt: no input media (set 'input' or the pipeline input)")
        model = params.get("model", "base")
        lang = params.get("lang", "auto")

        provider = providers.default_stt(model=model)
        transcript = provider.transcribe(
            media,
            lang=lang,
            vad_filter=params.get("vad_filter", True),
            beam_size=params.get("beam_size", 5),
        )

        words = [w.as_dict() for seg in transcript.segments for w in seg.words]
        return BlockResult(
            outputs={
                "text": transcript.text,
                "segments": [s.as_dict() for s in transcript.segments],
                "words": words,
                "lang": transcript.lang,
            }
        )
