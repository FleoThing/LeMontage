"""Confine pipeline-controlled output paths to a set of allowed roots.

A pipeline file (and therefore the ``output:`` path of an ``export``/``concat``
step) may come from an untrusted source. Left unchecked, ``output:
../../etc/cron.d/x`` or an absolute path lets a shared pipeline write a file
anywhere the process can reach. :func:`confine` resolves the candidate and
rejects it unless it lands under one of the allowed roots (the pipeline's output
directory or the current working directory).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when an output path escapes every allowed root."""


def confine(candidate: Path, allowed_roots: Iterable[Path]) -> Path:
    """Return ``candidate`` resolved, or raise if it escapes ``allowed_roots``.

    A path is allowed when, once resolved (symlinks and ``..`` collapsed), it is
    one of the roots or lives under one of them.
    """
    resolved = candidate.resolve()
    for root in allowed_roots:
        root = root.resolve()
        if resolved == root or root in resolved.parents:
            return resolved
    roots = ", ".join(str(Path(r).resolve()) for r in allowed_roots)
    raise UnsafePathError(
        f"output path '{candidate}' escapes the allowed output directory (must stay under: {roots})"
    )


def allowed_roots(output_dir: Path) -> list[Path]:
    """The roots an output path may live under: the output dir and the CWD."""
    return [output_dir, Path.cwd()]
