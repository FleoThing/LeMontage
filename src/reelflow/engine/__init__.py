"""The Reelflow execution engine.

Turns a validated pipeline document into produced media files. The public entry
point is :func:`run_pipeline`, which builds the DAG, then executes it.
"""

from __future__ import annotations

from .executor import RunResult, run_pipeline

__all__ = ["run_pipeline", "RunResult"]
