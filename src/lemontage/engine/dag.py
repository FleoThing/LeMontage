"""Build an execution DAG from a pipeline's ordered step list.

Each step becomes a :class:`Node`. Edges are inferred from three sources:

1. **Template refs** — ``{{ steps.<id>.* }}`` makes this step depend on ``<id>``.
2. **Channel wiring** — a ``from: X`` consumer depends on the ``emit: X`` producer.
3. **Channel chaining** — consumers of the same channel keep their listed order
   (so ``cut`` → ``captions`` → ``export`` run in sequence per item).

:func:`build_dag` returns the nodes in a stable topological order and raises
:class:`DagError` on a missing reference or a dependency cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import spec

_STEP_REF = re.compile(r"\{\{\s*steps\.([A-Za-z0-9_-]+)")


class DagError(ValueError):
    """Raised for unresolvable references or cycles in the step graph."""


@dataclass
class Node:
    """A single step, with its block name, parameters and wiring."""

    index: int
    step_id: str
    block: str
    params: dict[str, Any]
    common: dict[str, Any]
    emits: str | None = None
    consumes: str | None = None
    deps: set[int] = field(default_factory=set)


def build_nodes(steps: list[dict[str, Any]]) -> list[Node]:
    """Turn raw step mappings into :class:`Node`s (no edges yet)."""
    nodes: list[Node] = []
    for index, step in enumerate(steps):
        block_keys = [k for k in step if k not in spec.COMMON_STEP_FIELDS]
        block = block_keys[0]
        params = step.get(block) or {}
        common = {k: step[k] for k in step if k in spec.COMMON_STEP_FIELDS}
        step_id = step.get("id", block)
        emit = params.get("emit") if isinstance(params, dict) else None
        consume = params.get("from") if isinstance(params, dict) else None
        nodes.append(
            Node(
                index=index,
                step_id=step_id,
                block=block,
                params=params if isinstance(params, dict) else {},
                common=common,
                emits=emit if isinstance(emit, str) else None,
                consumes=consume if isinstance(consume, str) else None,
            )
        )
    return nodes


def build_dag(steps: list[dict[str, Any]]) -> list[Node]:
    """Build the DAG and return nodes in a stable topological order."""
    nodes = build_nodes(steps)
    by_id = {n.step_id: n for n in nodes}
    emitters = {n.emits: n for n in nodes if n.emits}

    _add_template_edges(nodes, by_id)
    _add_channel_edges(nodes, emitters)

    return _topo_sort(nodes)


def _add_template_edges(nodes: list[Node], by_id: dict[str, Node]) -> None:
    for node in nodes:
        for ref_id in _referenced_step_ids(node.params) | _referenced_step_ids(node.common):
            target = by_id.get(ref_id)
            if target is None:
                raise DagError(f"step '{node.step_id}' references unknown step '{ref_id}'")
            if target.index != node.index:
                node.deps.add(target.index)


def _add_channel_edges(nodes: list[Node], emitters: dict[str, Node]) -> None:
    # Consumer depends on the producer of its channel...
    for node in nodes:
        if node.consumes:
            producer = emitters.get(node.consumes)
            if producer is None:
                raise DagError(f"step '{node.step_id}' consumes unknown channel '{node.consumes}'")
            node.deps.add(producer.index)

    # ...and consumers of the same channel keep their listed order.
    consumers: dict[str, list[Node]] = {}
    for node in sorted(nodes, key=lambda n: n.index):
        if node.consumes:
            consumers.setdefault(node.consumes, []).append(node)
    for chain in consumers.values():
        for prev, curr in zip(chain, chain[1:], strict=False):
            curr.deps.add(prev.index)


def _referenced_step_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_STEP_REF.findall(value))
    elif isinstance(value, list):
        for item in value:
            found |= _referenced_step_ids(item)
    elif isinstance(value, dict):
        for item in value.values():
            found |= _referenced_step_ids(item)
    return found


def _topo_sort(nodes: list[Node]) -> list[Node]:
    """Kahn's algorithm, breaking ties by original index for determinism."""
    by_index = {n.index: n for n in nodes}
    indegree = {n.index: len(n.deps) for n in nodes}
    dependents: dict[int, list[int]] = {n.index: [] for n in nodes}
    for node in nodes:
        for dep in node.deps:
            dependents[dep].append(node.index)

    ready = sorted(i for i, deg in indegree.items() if deg == 0)
    order: list[Node] = []
    while ready:
        index = ready.pop(0)
        order.append(by_index[index])
        for dep in dependents[index]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
        ready.sort()

    if len(order) != len(nodes):
        cyclic = sorted(i for i, deg in indegree.items() if deg > 0)
        names = ", ".join(by_index[i].step_id for i in cyclic)
        raise DagError(f"dependency cycle among steps: {names}")
    return order
