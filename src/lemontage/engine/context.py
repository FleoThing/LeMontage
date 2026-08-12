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
    # Set only when this cell shares the run with others (see below), and only
    # for a matrix run — a plain pipeline keeps the historical work path, so
    # upgrading does not invalidate everyone's checkpoints.
    cell_key: str = ""
    # Ceiling on how many channel items this cell may render at once, so that
    # concurrent cells cannot multiply the machine's worker budget between them.
    # ``None`` means "no sharing, take the whole budget".
    worker_budget: int | None = None

    def work_dir(self) -> Path:
        """Directory for intermediate artifacts (created on demand).

        Every block names its intermediates ``<step_id>-<index>.mp4`` inside this
        directory, and those names carry nothing about the matrix cell. That was
        harmless while cells ran one after another — each simply overwrote the
        previous cell's scratch after it had been consumed — but two cells running
        at once would write the same ``cut-0.mp4``. Hence the per-cell
        subdirectory: the isolation the cache already had, extended to the files.
        """
        wd = self.output_dir / ".lemontage" / "work"
        if self.cell_key:
            wd = wd / self.cell_key
        wd.mkdir(parents=True, exist_ok=True)
        return wd
