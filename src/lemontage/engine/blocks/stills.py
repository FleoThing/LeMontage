"""``stills`` — turn a folder of images into a channel of shots (SPEC §6.10).

A producer block (the image counterpart of ``detect_clips``): it lists a folder
of images, orders them, and emits one channel item per image::

    {index, image: <path>, duration: <seconds>}

A downstream ``still`` renders each item into a short video clip so the existing
``export`` / ``concat`` (transitions) blocks can build a slideshow.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...spec import SUPPORTED_IMAGE_EXTENSIONS
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult

_DEFAULT_DURATION = "3s"


class StillsBlock(Block):
    name = "stills"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        source = params.get("input") or ctx.input.get("source")
        if not source:
            raise ValueError("stills: no image folder (set 'input' or the pipeline input.source)")
        images = _list_images(source)
        if params.get("shuffle"):
            images = _shuffled(images, int(params.get("seed", 0)))
        if params.get("max"):
            images = images[: int(params["max"])]
        if not images:
            raise ValueError(f"stills: no images found in '{source}'")

        duration = parse_seconds(params.get("duration", _DEFAULT_DURATION))
        items = [
            {"index": i, "image": str(path), "duration": duration} for i, path in enumerate(images)
        ]
        return BlockResult(outputs={"count": len(items)}, channel_items=items)


def _list_images(source: str) -> list[Path]:
    """Return the image files of a folder, natural-sorted by name."""
    folder = Path(source)
    if not folder.is_dir():
        raise ValueError(f"stills: '{source}' is not a folder")
    images = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    return sorted(images, key=_natural_key)


def _natural_key(path: Path) -> list[Any]:
    """Sort key so ``img2`` comes before ``img10`` (digits compared as numbers)."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", path.name)]


def _shuffled(images: list[Path], seed: int) -> list[Path]:
    """Deterministic shuffle (seeded) — no global RNG, safe under parallelism."""
    import random

    rng = random.Random(seed)
    shuffled = list(images)
    rng.shuffle(shuffled)
    return shuffled
