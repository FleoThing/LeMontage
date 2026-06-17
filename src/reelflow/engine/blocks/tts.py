"""``tts`` — synthesize speech from text (SPEC §6.2)."""

from __future__ import annotations

from typing import Any

from .. import providers
from ..context import RunContext
from .base import Block, BlockResult


class TtsBlock(Block):
    name = "tts"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        text = params.get("text")
        if not text:
            raise ValueError("tts: 'text' is required")
        voice = params.get("voice", "default")
        speed = params.get("speed", 1.0)

        out_path = ctx.work_dir() / f"{step_id}.wav"

        provider = providers.default_tts()
        result = provider.synthesize(text, out_path, voice=voice, speed=speed)

        return BlockResult(
            outputs={"audio": str(result.audio), "duration": result.duration}
        )
