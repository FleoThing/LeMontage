"""Built-in block implementations and their registry."""

from __future__ import annotations

from .base import Block, BlockResult, ItemResult
from .captions import CaptionsBlock
from .concat import ConcatBlock
from .cut import CutBlock
from .detect_clips import DetectClipsBlock
from .export import ExportBlock
from .reverse import ReverseBlock
from .stt import SttBlock

# Maps a block name to its implementation. The executor looks blocks up here.
REGISTRY: dict[str, Block] = {
    block.name: block
    for block in (
        SttBlock(),
        DetectClipsBlock(),
        CutBlock(),
        CaptionsBlock(),
        ExportBlock(),
        ConcatBlock(),
        ReverseBlock(),
    )
}

__all__ = ["Block", "BlockResult", "ItemResult", "REGISTRY"]
