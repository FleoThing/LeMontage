"""Block contract used by the executor.

A block runs in one of two modes:

* **single** — :meth:`Block.execute` runs once and returns a :class:`BlockResult`.
  If it sets ``channel_items``, those become the channel named by the step's
  ``emit:`` (a producer like ``detect_clips``).
* **mapped** — when the step has ``from: <channel>``, the executor calls
  :meth:`Block.execute_item` once per channel item, in parallel, and aggregates
  the per-item outputs into lists.
* **aggregator** — a block with ``maps = False`` receives the whole channel via
  :meth:`Block.execute_channel`. Its ``from`` may name a single channel or a
  **list** of channels (``from: [a, b]``): the executor merges them in listed
  order, re-indexes the items, and hands the block one flat list. An aggregator
  may also set ``channel_items`` on its result and carry an ``emit:``; the
  executor then publishes that channel, so a produced reel can feed a parent
  aggregator (nested sub-pipelines).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..context import RunContext


@dataclass
class BlockResult:
    """What a block produces in single mode."""

    outputs: dict[str, Any] = field(default_factory=dict)
    channel_items: list[dict[str, Any]] | None = None


@dataclass
class ItemResult:
    """What a block produces for one channel item in mapped mode."""

    # Fields merged into the channel item so later consumers see them.
    item: dict[str, Any] = field(default_factory=dict)
    # Per-item outputs; the executor collects each key into a list.
    outputs: dict[str, Any] = field(default_factory=dict)


class Block(ABC):
    """Base class for every built-in block."""

    name: str
    # When True, a `from:` consumer runs once per channel item (mapped). When
    # False, the block aggregates the whole channel via execute_channel().
    maps: bool = True

    @abstractmethod
    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        """Run the block once (single mode)."""

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        """Run the block for one channel item (mapped mode)."""
        raise NotImplementedError(f"block '{self.name}' cannot map over a channel")

    def execute_channel(
        self, params: dict[str, Any], items: list[dict[str, Any]], ctx: RunContext, step_id: str
    ) -> BlockResult:
        """Run the block once over the whole channel (aggregator mode)."""
        raise NotImplementedError(f"block '{self.name}' is not a channel aggregator")
