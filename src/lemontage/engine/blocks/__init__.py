"""Built-in block implementations and their registry."""

from __future__ import annotations

from .base import Block, BlockResult, ItemResult
from .captions import CaptionsBlock
from .compose import ComposeBlock
from .concat import ConcatBlock
from .cut import CutBlock
from .detect_clips import DetectClipsBlock
from .export import ExportBlock
from .filter import FilterBlock
from .music import MusicBlock
from .overlay import OverlayBlock
from .reverse import ReverseBlock
from .sfx import SfxBlock
from .speed import SpeedBlock
from .still import StillBlock
from .stills import StillsBlock
from .stt import SttBlock
from .zoom import ZoomBlock

# Maps a block name to its implementation. The executor looks blocks up here.
REGISTRY: dict[str, Block] = {
    block.name: block
    for block in (
        SttBlock(),
        DetectClipsBlock(),
        CutBlock(),
        CaptionsBlock(),
        ComposeBlock(),
        ExportBlock(),
        FilterBlock(),
        OverlayBlock(),
        ConcatBlock(),
        SpeedBlock(),
        ReverseBlock(),
        StillsBlock(),
        StillBlock(),
        MusicBlock(),
        ZoomBlock(),
        SfxBlock(),
    )
}

__all__ = ["REGISTRY", "Block", "BlockResult", "ItemResult"]
