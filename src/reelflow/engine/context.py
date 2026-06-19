"""Shared mutable state for a single pipeline run (one matrix cell)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    """Everything a step needs to resolve templates and locate I/O.

    One ``RunContext`` exists per matrix cell. ``step_outputs`` and ``channels``
    accumulate as steps run; ``state`` tracks each step's lifecycle state.
    """

    vars: dict[str, Any]
    input: dict[str, Any]
    matrix: dict[str, Any]
    output_dir: Path
    pipeline_name: str
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    channels: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    state: dict[str, str] = field(default_factory=dict)

    def work_dir(self) -> Path:
        """Directory for intermediate artifacts (created on demand)."""
        wd = self.output_dir / ".reelflow" / "work"
        wd.mkdir(parents=True, exist_ok=True)
        return wd
