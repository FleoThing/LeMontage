"""Execute a validated pipeline: states, cache, channels, matrix, logging.

The flow per matrix cell is:

1. Build the DAG (:mod:`.dag`) and walk it in topological order.
2. For each step, evaluate ``requires`` and the cache; if neither short-circuits,
   resolve its templated params and run the block — in single mode, or fanned out
   over a channel (parallel, one run per item).
3. Apply ``on_failure`` (``abort`` / ``skip`` / ``retry``).

States follow SPEC §5.2: ``pending → running → success | failed | skipped``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import template
from .blocks import REGISTRY
from .blocks.base import Block
from .context import RunContext
from .dag import Node, build_dag

# Step lifecycle states.
PENDING, RUNNING, SUCCESS, FAILED, SKIPPED = (
    "pending",
    "running",
    "success",
    "failed",
    "skipped",
)

Reporter = Callable[[str], None]


@dataclass
class CellResult:
    """Outcome of one matrix cell."""

    matrix: dict[str, Any]
    states: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and FAILED not in self.states.values()


@dataclass
class RunResult:
    """Outcome of a whole pipeline run (all matrix cells)."""

    cells: list[CellResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.cells) and all(c.ok for c in self.cells)


class ExecutionError(RuntimeError):
    """Raised when a step fails and its ``on_failure`` is ``abort``."""


def run_pipeline(
    doc: dict[str, Any],
    *,
    var_overrides: dict[str, Any] | None = None,
    reporter: Reporter | None = None,
    clean: bool | None = None,
) -> RunResult:
    """Run a validated pipeline document and return per-cell results.

    ``clean`` removes the temp dir (``output/.lemontage``) after a successful run.
    ``None`` defers to the pipeline's ``output.cleanup`` flag; ``True``/``False``
    override it (e.g. the CLI ``--clean``).
    """
    report = reporter or _default_reporter
    cells = _matrix_cells(doc.get("matrix"))
    nodes = build_dag(doc["steps"])
    result = RunResult()

    for cell in cells:
        if cell:
            report(f"━━ matrix {_cell_label(cell)} ━━")
        cell_result = _run_cell(doc, nodes, cell, var_overrides or {}, report)
        result.cells.append(cell_result)

    if result.ok and _should_clean(doc, clean):
        _cleanup(doc, result, report)

    return result


def _should_clean(doc: dict[str, Any], clean: bool | None) -> bool:
    if clean is not None:
        return clean
    return bool((doc.get("output") or {}).get("cleanup", False))


def _cleanup(doc: dict[str, Any], result: RunResult, report: Reporter) -> None:
    """Remove the temp dir, plus per-clip files that a concat already merged."""
    import shutil

    output_dir = Path((doc.get("output") or {}).get("dir", "./output"))
    temp = output_dir / ".lemontage"
    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)

    removed = _remove_merged_parts(result, output_dir)
    extra = f" + {removed} intermediate clip(s)" if removed else ""
    report(f"🧹 cleaned temp files in {temp}{extra}")


def _remove_merged_parts(result: RunResult, output_dir: Path) -> int:
    """Delete export clips consumed by a concat (kept: the final reel)."""
    base = output_dir.resolve()
    removed = 0
    for cell in result.cells:
        for outputs in cell.outputs.values():
            # A concat step exposes the merged 'file' and the source 'parts'.
            if not (isinstance(outputs, dict) and outputs.get("file") and "parts" in outputs):
                continue
            reel = str(outputs["file"])
            for part in outputs.get("parts") or []:
                path = Path(part)
                if str(part) == reel or not path.exists():
                    continue
                if base in path.resolve().parents:  # safety: only under output dir
                    path.unlink()
                    removed += 1
    return removed


def _run_cell(
    doc: dict[str, Any],
    nodes: list[Node],
    matrix: dict[str, Any],
    var_overrides: dict[str, Any],
    report: Reporter,
) -> CellResult:
    base_vars = {**(doc.get("vars") or {}), **var_overrides}
    output_dir = Path((doc.get("output") or {}).get("dir", "./output"))
    ctx = RunContext(
        vars=base_vars,
        input=doc.get("input") or {},
        matrix=matrix,
        output_dir=output_dir,
        pipeline_name=str(doc.get("name", "pipeline")),
    )
    cell = CellResult(matrix=matrix)
    for node in nodes:
        ctx.state[node.step_id] = PENDING

    cache = _Cache(output_dir, matrix)
    for node in nodes:
        try:
            _run_node(node, ctx, cache, report)
        except ExecutionError as exc:
            cell.error = str(exc)
            break
    cell.states = dict(ctx.state)
    cell.outputs = dict(ctx.step_outputs)
    return cell


def _run_node(node: Node, ctx: RunContext, cache: _Cache, report: Reporter) -> None:
    if not _requires_met(node, ctx):
        ctx.state[node.step_id] = SKIPPED
        report(f"  ⊘ {node.step_id} ({node.block}) — skipped, requires unmet")
        return

    params = template.resolve(node.params, ctx)
    signature = cache.signature(node, params)

    if node.common.get("cache", True) and cache.load(node, signature, ctx):
        # A cache hit reused a prior successful result, so it counts as success
        # for downstream `requires` gates — only the recompute is skipped.
        ctx.state[node.step_id] = SUCCESS
        report(f"  ⊙ {node.step_id} ({node.block}) — cached")
        return

    ctx.state[node.step_id] = RUNNING
    block = REGISTRY[node.block]
    attempts = _max_attempts(node)
    report(f"  → {node.step_id} ({node.block}) running…")

    for attempt in range(1, attempts + 1):
        try:
            _execute(node, block, params, ctx)
            ctx.state[node.step_id] = SUCCESS
            cache.save(node, signature, ctx)
            report(f"  ✓ {node.step_id} ({node.block})")
            return
        except Exception as exc:  # noqa: BLE001 - the engine owns failure policy
            on_failure = node.common.get("on_failure", "abort")
            if on_failure == "retry" and attempt < attempts:
                report(f"  ↻ {node.step_id} ({node.block}) — retry {attempt}/{attempts - 1}")
                continue
            if on_failure == "skip":
                ctx.state[node.step_id] = SKIPPED
                report(f"  ⊘ {node.step_id} ({node.block}) — failed, skipped: {exc}")
                return
            ctx.state[node.step_id] = FAILED
            report(f"  ✗ {node.step_id} ({node.block}) — {exc}")
            raise ExecutionError(f"step '{node.step_id}' failed: {exc}") from exc


def _execute(node: Node, block: Block, params: dict[str, Any], ctx: RunContext) -> None:
    if node.consumes and block.maps:
        _execute_mapped(node, block, params, ctx)
    elif node.consumes_list:  # channel aggregator (e.g. concat): gets whole channel(s)
        items = _gather_channels(node.consumes_list, ctx)
        result = block.execute_channel(params, items, ctx, node.step_id)
        ctx.step_outputs[node.step_id] = result.outputs
        # An aggregator may itself `emit:` its result as a channel (a finished
        # reel as one item), so a parent concat can join it with other reels.
        if node.emits and result.channel_items is not None:
            ctx.channels[node.emits] = result.channel_items
    else:
        result = block.execute(params, ctx, node.step_id)
        ctx.step_outputs[node.step_id] = result.outputs
        if node.emits and result.channel_items is not None:
            ctx.channels[node.emits] = result.channel_items


def _gather_channels(channels: list[str], ctx: RunContext) -> list[dict[str, Any]]:
    """Merge one or more channels into a single ordered, re-indexed item list.

    Channels are joined in the order listed in ``from``; within each channel the
    existing ``index`` order is kept. Items are copied and re-indexed sequentially
    so a downstream sort-by-index preserves this order (and the per-channel
    ``index`` collisions — every channel starts at 0 — don't interleave clips).
    Empty or absent channels simply contribute nothing.
    """
    merged: list[dict[str, Any]] = []
    for channel in channels:
        chan_items = sorted(ctx.channels.get(channel, []), key=lambda it: it.get("index", 0))
        # Tag each item with its source channel so an aggregator can tell where
        # one channel ends and the next begins (e.g. transitions only at joins).
        merged.extend({**item, "_channel": channel} for item in chan_items)
    return [{**item, "index": i} for i, item in enumerate(merged)]


def _execute_mapped(node: Node, block: Block, params: dict[str, Any], ctx: RunContext) -> None:
    items = ctx.channels.get(node.consumes, [])
    if not items:
        ctx.step_outputs[node.step_id] = {}
        return

    def work(item: dict[str, Any]):
        return item, block.execute_item(params, item, ctx, node.step_id)

    aggregated: dict[str, list[Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        results = list(pool.map(work, items))

    for item, item_result in results:
        item.update(item_result.item)  # later consumers see the new fields
        for key, value in item_result.outputs.items():
            aggregated.setdefault(key, []).append(value)

    ctx.step_outputs[node.step_id] = aggregated


def _requires_met(node: Node, ctx: RunContext) -> bool:
    requires = node.common.get("requires")
    if not requires:
        return True
    step_id, _, wanted = str(requires).rpartition(".")
    if not step_id:
        return True
    return ctx.state.get(step_id) == wanted


def _max_attempts(node: Node) -> int:
    if node.common.get("on_failure") != "retry":
        return 1
    return 1 + int(node.common.get("retries", 0))


def _matrix_cells(matrix: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not matrix:
        return [{}]
    keys = list(matrix)
    value_lists = [matrix[k] if isinstance(matrix[k], list) else [matrix[k]] for k in keys]
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*value_lists)]


def _cell_label(cell: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in cell.items())


def _default_reporter(message: str) -> None:
    import sys

    print(message, file=sys.stderr)


class _Cache:
    """Per-cell checkpoint store under ``<output>/.lemontage/cache/``."""

    def __init__(self, output_dir: Path, matrix: dict[str, Any]) -> None:
        self._dir = output_dir / ".lemontage" / "cache"
        self._cell_key = _signature_str(matrix) if matrix else "default"

    def _path(self, node: Node) -> Path:
        return self._dir / f"{self._cell_key}-{node.step_id}.json"

    def signature(self, node: Node, params: dict[str, Any]) -> str:
        return _signature_str({"block": node.block, "params": params})

    def load(self, node: Node, signature: str, ctx: RunContext) -> bool:
        path = self._path(node)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if data.get("signature") != signature:
            return False
        if not _outputs_files_exist(data.get("outputs", {})):
            return False

        ctx.step_outputs[node.step_id] = data.get("outputs", {})
        channel = data.get("channel")
        if node.emits and channel is not None:
            ctx.channels[node.emits] = channel
        if node.consumes and channel is not None:
            ctx.channels[node.consumes] = channel
        return True

    def save(self, node: Node, signature: str, ctx: RunContext) -> None:
        channel = None
        if node.emits:
            channel = ctx.channels.get(node.emits)
        elif node.consumes:
            channel = ctx.channels.get(node.consumes)
        payload = {
            "signature": signature,
            "outputs": ctx.step_outputs.get(node.step_id, {}),
            "channel": channel,
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(node).write_text(json.dumps(payload), encoding="utf-8")


def _signature_str(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _outputs_files_exist(outputs: dict[str, Any]) -> bool:
    """Every output that looks like a produced file path must still exist."""
    for value in outputs.values():
        for candidate in value if isinstance(value, list) else [value]:
            if isinstance(candidate, str) and _looks_like_path(candidate):
                if not Path(candidate).exists():
                    return False
    return True


def _looks_like_path(value: str) -> bool:
    return value.endswith((".mp4", ".wav", ".srt", ".mov", ".mkv", ".mp3"))
