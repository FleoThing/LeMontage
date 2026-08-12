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
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import spec
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

    shared = _shareable_cells(doc, cells, report)
    if shared > 1:
        result.cells = _run_cells_concurrently(
            doc, nodes, cells, var_overrides or {}, report, shared
        )
    else:
        for cell in cells:
            if cell:
                report(f"━━ matrix {_cell_label(cell)} ━━")
            result.cells.append(_run_cell(doc, nodes, cell, var_overrides or {}, report))

    if result.ok and _should_clean(doc, clean):
        _cleanup(doc, result, report)

    return result


# Blocks that write a *deliverable* into the output directory. Their default file
# names are built from the pipeline name, the step id and the clip index — none of
# which vary by matrix cell — so two cells collide there unless the pipeline gave
# them an explicit, cell-dependent `output:`.
_OUTPUT_DIR_BLOCKS = frozenset({"export", "concat", "music"})
_MATRIX_REF = re.compile(r"\{\{\s*matrix\.")


def _shareable_cells(doc: dict[str, Any], cells: list[dict[str, Any]], report: Reporter) -> int:
    """How many matrix cells may run at once. 1 means "run them in order".

    Concurrency is only safe when the cells write different files. Intermediates
    are handled by the engine (a per-cell work directory), but deliverables are
    named by the pipeline, and the defaults carry nothing about the cell: four
    cells would all render `<name>-0.mp4`. Sequentially that merely overwrites
    scratch that has already been consumed; concurrently it is a race on one file.

    So a pipeline qualifies only when every step that writes a deliverable has an
    explicit `output:` mentioning `{{ matrix.… }}`. That test is exact rather than
    merely cautious: a cell differs from its siblings by its matrix values and
    nothing else, so a path that does not mention them cannot differ either.

    A pipeline that does not qualify runs exactly as it always has, and is told
    why — silently rendering it half as fast as it could be would be worse.
    """
    if len(cells) < 2:
        return 1
    blockers = [
        step_id
        for step_id, block, params in _steps_with_params(doc)
        if block in _OUTPUT_DIR_BLOCKS and not _MATRIX_REF.search(str(params.get("output", "")))
    ]
    if blockers:
        report(
            f"  ⓘ matrix cells run in order: {', '.join(blockers)} would write the same "
            "file in every cell. Give them an 'output:' containing {{ matrix.<key> }} "
            "to render the cells concurrently."
        )
        return 1
    return min(len(cells), _pool_size(len(cells)))


def _steps_with_params(doc: dict[str, Any]):
    """``(step_id, block, params)`` for each step, skipping malformed ones."""
    for step in doc.get("steps") or []:
        if not isinstance(step, dict):
            continue
        block_keys = [k for k in step if k not in spec.COMMON_STEP_FIELDS]
        if len(block_keys) != 1:
            continue
        params = step.get(block_keys[0])
        yield (
            str(step.get("id", block_keys[0])),
            block_keys[0],
            params if isinstance(params, dict) else {},
        )


def _run_cells_concurrently(
    doc: dict[str, Any],
    nodes: list[Node],
    cells: list[dict[str, Any]],
    var_overrides: dict[str, Any],
    report: Reporter,
    workers: int,
) -> list[CellResult]:
    """Render the cells at once, and report them as if they had run in order.

    Two things are deliberately not left to chance:

    * **Logs.** Each cell writes into its own buffer instead of straight to the
      reporter, so two cells cannot interleave their step lines.
    * **Order.** ``pool.map`` yields in the order the cells were declared, whatever
      order they finish in, so both the report and ``RunResult.cells`` follow the
      matrix as written.

    Each cell also gets a slice of the worker budget rather than the whole of it:
    four cells each opening a full pool would multiply the machine's concurrency
    by four.
    """
    budget = max(1, _pool_size(len(cells) * _MIN_WORKERS) // workers)

    def work(cell: dict[str, Any]) -> tuple[dict[str, Any], list[str], CellResult]:
        lines: list[str] = []
        return cell, lines, _run_cell(doc, nodes, cell, var_overrides, lines.append, budget)

    results: list[CellResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for cell, lines, cell_result in pool.map(work, cells):
            report(f"━━ matrix {_cell_label(cell)} ━━")
            for line in lines:
                report(line)
            results.append(cell_result)
    return results


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
    worker_budget: int | None = None,
) -> CellResult:
    base_vars = {**(doc.get("vars") or {}), **var_overrides}
    output_dir = Path((doc.get("output") or {}).get("dir", "./output"))
    ctx = RunContext(
        vars=base_vars,
        input=doc.get("input") or {},
        matrix=matrix,
        output_dir=output_dir,
        pipeline_name=str(doc.get("name", "pipeline")),
        # Only a cell sharing the run with others needs its own scratch directory.
        # A sequential run keeps the historical path, so upgrading LeMontage does
        # not throw away a working checkpoint cache.
        cell_key=_signature_str(matrix) if (matrix and worker_budget) else "",
        worker_budget=worker_budget,
    )
    # Resolve templates in the input against vars/matrix (steps haven't run yet),
    # so a reusable pipeline can take its source via `--var` (e.g.
    # `input.source: "{{ vars.source }}"`).
    ctx.input = template.resolve(ctx.input, ctx)
    cell = CellResult(matrix=matrix)
    for node in nodes:
        ctx.state[node.step_id] = PENDING

    cache = _Cache(output_dir, matrix)
    cell.error = _run_steps(nodes, ctx, cache, report)
    cell.states = dict(ctx.state)
    cell.outputs = dict(ctx.step_outputs)
    return cell


def _run_steps(nodes: list[Node], ctx: RunContext, cache: _Cache, report: Reporter) -> str | None:
    """Walk the DAG, running whatever has no unfinished dependency left.

    The steps used to run strictly in topological order, one at a time, although
    ``Node.deps`` and the topological sort already knew which of them were
    independent. Now every node whose dependencies are done starts immediately,
    so two branches of a fan-out overlap instead of queueing.

    Most pipelines feel nothing: a short-form edit is a chain (``stt`` →
    ``detect_clips`` → ``cut`` → ``export`` → ``captions`` → ``concat``) and a
    chain has nothing to overlap. This pays on multi-source and multi-channel
    edits, where two independent branches meet only at a final ``concat``.

    Three things this has to keep, which the plain sequence gave for free:

    * **Abort.** ``on_failure: abort`` stopped the loop with a ``break``. Here it
      stops *scheduling*: nothing new is dispatched, the work already in flight is
      awaited rather than orphaned, and the first error raised is the one reported.
    * **Report order.** Each node writes into its own buffer, flushed as a block
      when it finishes, so two concurrent steps cannot interleave their lines.
    * **Bookkeeping.** Signatures, cache entries and step states are touched under
      one lock. It is held for microseconds and never across a block's execution,
      so it costs nothing and removes every question about two nodes finishing at
      the same moment.
    """
    pending = {node.index: set(node.deps) for node in nodes}
    by_index = {node.index: node for node in nodes}
    dependents: dict[int, list[int]] = {node.index: [] for node in nodes}
    for node in nodes:
        for dep in node.deps:
            dependents[dep].append(node.index)

    lock = threading.Lock()
    error: str | None = None
    ready = sorted(i for i, deps in pending.items() if not deps)

    def work(index: int) -> tuple[int, list[str], ExecutionError | None]:
        lines: list[str] = []
        try:
            _run_node(by_index[index], ctx, cache, lines.append, lock)
        except ExecutionError as exc:
            return index, lines, exc
        return index, lines, None

    with ThreadPoolExecutor(max_workers=_pool_size(len(nodes))) as pool:
        running = {pool.submit(work, i): i for i in ready}
        while running:
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.pop(future)
                index, lines, failure = future.result()
                for line in lines:
                    report(line)
                if failure is not None:
                    # Keep the first failure, and schedule nothing further. The
                    # steps still in flight are left to finish on their own.
                    error = error or str(failure)
                    continue
                if error is not None:
                    continue
                for dependent in dependents[index]:
                    pending[dependent].discard(index)
                    if not pending[dependent]:
                        running[pool.submit(work, dependent)] = dependent
    return error


def _run_node(
    node: Node,
    ctx: RunContext,
    cache: _Cache,
    report: Reporter,
    lock: threading.Lock | None = None,
) -> None:
    """Run one step. ``lock`` guards the shared bookkeeping when steps overlap.

    It is taken around signature/cache/state work only — never around
    :func:`_execute`, which is where all the time goes.
    """
    guard = lock or nullcontext()

    with guard:
        if not _requires_met(node, ctx):
            ctx.state[node.step_id] = SKIPPED
            report(f"  ⊘ {node.step_id} ({node.block}) — skipped, requires unmet")
            return

        params = template.resolve(node.params, ctx)
        signature = cache.signature(node, params, ctx.input.get("source"))

        if (
            node.common.get("cache", True)
            and not (node.deps & cache.reran)  # an upstream re-ran -> our inputs changed
            and cache.load(node, signature, ctx)
        ):
            # A cache hit reused a prior successful result, so it counts as success
            # for downstream `requires` gates — only the recompute is skipped.
            ctx.state[node.step_id] = SUCCESS
            report(f"  ⊙ {node.step_id} ({node.block}) — cached")
            return

        cache.reran.add(node.index)
        ctx.state[node.step_id] = RUNNING

    block = REGISTRY[node.block]
    attempts = _max_attempts(node)
    report(f"  → {node.step_id} ({node.block}) running…")

    for attempt in range(1, attempts + 1):
        try:
            _execute(node, block, params, ctx)
            with guard:
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
                with guard:
                    ctx.state[node.step_id] = SKIPPED
                report(f"  ⊘ {node.step_id} ({node.block}) — failed, skipped: {exc}")
                return
            with guard:
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

    workers = _pool_size(len(items))
    if ctx.worker_budget is not None:
        workers = max(1, min(workers, ctx.worker_budget))

    aggregated: dict[str, list[Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, items))

    for item, item_result in results:
        item.update(item_result.item)  # later consumers see the new fields
        for key, value in item_result.outputs.items():
            aggregated.setdefault(key, []).append(value)

    ctx.step_outputs[node.step_id] = aggregated


# Floor for the channel worker pool. It was the whole of the old
# `min(8, len(items))`, and measurement says it is well placed: on the
# `benchmarks/channel.yaml` control (16 clips, 4 cores) the medians were 38.30s at
# 2 workers, 36.14s at 4, 35.72s at 8, 35.45s at 16 — monotone, and flat past 8.
# So the arbitrary constant was not the problem. Not scaling *up* was.
_MIN_WORKERS = 8


def _pool_size(item_count: int) -> int:
    """How many channel items to render at once.

    The expectation going in was that 8 oversubscribes a small machine — every
    worker starts an ffmpeg that already takes every core for its own encoding, so
    on four cores this is eight concurrent encodes over four cores. Measured, that
    costs nothing: the encodes spend enough time blocked that the kernel packs
    them fine, and sizing the pool *down* to ``os.cpu_count()`` came out 1.2%
    slower, not faster.

    What the same curve does show is the opposite failure: 8 is a ceiling as well
    as a floor, and on a 32-core box it would leave most of the machine idle. So
    the floor stays and the pool grows with the machine.

    ``LEMONTAGE_WORKERS`` overrides it, for the machine where this is wrong. An
    environment variable and not a pipeline field, deliberately: how many encodes
    to run at once is a property of the machine, not of the edit — a pipeline from
    the hub has to render the same on a laptop and on a build box.
    """
    override = os.environ.get("LEMONTAGE_WORKERS", "").strip()
    if override:
        try:
            wanted = int(override)
        except ValueError:
            raise ValueError(
                f"LEMONTAGE_WORKERS must be a positive integer, got '{override}'"
            ) from None
        if wanted < 1:
            raise ValueError(f"LEMONTAGE_WORKERS must be >= 1, got {wanted}")
    else:
        wanted = max(_MIN_WORKERS, os.cpu_count() or 1)
    return max(1, min(item_count, wanted))


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
        # Signatures computed this run (by node index) and the nodes that
        # actually re-executed, so invalidation propagates downstream.
        self._sigs: dict[int, str] = {}
        self.reran: set[int] = set()

    def _path(self, node: Node) -> Path:
        return self._dir / f"{self._cell_key}-{node.step_id}.json"

    def signature(self, node: Node, params: dict[str, Any], source: Any = None) -> str:
        # Include the pipeline input source: a block that reads it (e.g. `stt`)
        # keeps `source` out of its params, so without this two different input
        # videos with identical params would collide on one cache entry.
        # Also chain in the parents' signatures, so a param change anywhere
        # upstream invalidates every dependent step's cache entry.
        parents = [self._sigs[i] for i in sorted(node.deps) if i in self._sigs]
        sig = _signature_str(
            {
                "block": node.block,
                "params": params,
                "source": _source_stamp(source),
                "parents": parents,
            }
        )
        self._sigs[node.index] = sig
        return sig

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


def _source_stamp(source: Any) -> Any:
    """The input source as the cache should see it: path **plus** size and mtime.

    Keying on the path alone made the checkpoint lie whenever the file behind it
    changed — re-cut an excerpt to the same name and every step replayed the old
    run's transcript and clips. Size + mtime catches that without reading (and
    hashing) a multi-gigabyte video on every step.
    """
    if not isinstance(source, str):
        return source
    try:
        stat = Path(source).stat()
    except OSError:  # not a local file (or gone) — the path is all we have
        return source
    return [source, stat.st_size, int(stat.st_mtime)]


def _signature_str(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _outputs_files_exist(outputs: dict[str, Any]) -> bool:
    """Every output that looks like a produced file path must still exist."""
    for value in outputs.values():
        for candidate in value if isinstance(value, list) else [value]:
            if (
                isinstance(candidate, str)
                and _looks_like_path(candidate)
                and not Path(candidate).exists()
            ):
                return False
    return True


def _looks_like_path(value: str) -> bool:
    return value.endswith((".mp4", ".wav", ".srt", ".mov", ".mkv", ".mp3"))
