"""Double-brace templating for pipeline values (see SPEC §3).

A template reference is ``{{ namespace.path }}``. Four namespaces are resolved:

* ``vars.*``       — the ``vars`` block, overridable from the CLI (``--var k=v``)
* ``input.*``      — fields of the ``input`` block
* ``steps.<id>.*`` — a named output of a step that already ran
* ``matrix.*``     — the current matrix cell

Resolution is **lazy**: it happens when a step is about to run, so a step can
reference any output produced before it.

A string that is *exactly* one reference (``"{{ steps.x.segments }}"``) resolves
to the referenced object itself (list, dict, int…). A reference embedded in
surrounding text is stringified and interpolated.
"""

from __future__ import annotations

import re
from typing import Any

_REF = re.compile(r"\{\{\s*(.*?)\s*\}\}")


class TemplateError(ValueError):
    """Raised when a template reference cannot be resolved."""


def resolve(value: Any, ctx: "Resolvable") -> Any:
    """Recursively resolve every template reference inside ``value``."""
    if isinstance(value, str):
        return _resolve_str(value, ctx)
    if isinstance(value, list):
        return [resolve(item, ctx) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, ctx) for key, item in value.items()}
    return value


def _resolve_str(text: str, ctx: "Resolvable") -> Any:
    whole = _REF.fullmatch(text.strip())
    if whole:
        return _lookup(whole.group(1), ctx)

    def replace(match: re.Match[str]) -> str:
        return _stringify(_lookup(match.group(1), ctx))

    return _REF.sub(replace, text)


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _lookup(expr: str, ctx: "Resolvable") -> Any:
    # Export-only placeholders (SPEC §6.6): `name` is the pipeline name; `index`
    # (0-based) and `part` (1-based) are the per-item position, only known when
    # export maps a channel — leave them verbatim for export to substitute later.
    if expr == "name":
        return ctx.pipeline_name
    if expr in ("index", "part"):
        return f"{{{{ {expr} }}}}"

    parts = expr.split(".")
    namespace, path = parts[0], parts[1:]

    if namespace == "vars":
        root: Any = ctx.vars
    elif namespace == "input":
        root = ctx.input
    elif namespace == "matrix":
        root = ctx.matrix
    elif namespace == "steps":
        if not path:
            raise TemplateError("'steps' reference needs a step id, e.g. steps.x.text")
        step_id, path = path[0], path[1:]
        try:
            root = ctx.step_outputs[step_id]
        except KeyError as exc:
            raise TemplateError(
                f"reference to step '{step_id}' which has no output (not run yet?)"
            ) from exc
    else:
        raise TemplateError(f"unknown template namespace '{namespace}' in '{{{{ {expr} }}}}'")

    return _walk(root, path, expr)


def _walk(root: Any, path: list[str], expr: str) -> Any:
    current = root
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise TemplateError(f"cannot resolve '{{{{ {expr} }}}}': no key '{key}'")
    return current


class Resolvable:
    """Minimal view of the run context the resolver needs.

    The executor's :class:`~reelflow.engine.context.RunContext` satisfies this
    duck-typed contract; keeping it explicit makes the resolver unit-testable.
    """

    vars: dict[str, Any]
    input: dict[str, Any]
    matrix: dict[str, Any]
    pipeline_name: str
    step_outputs: dict[str, dict[str, Any]]
