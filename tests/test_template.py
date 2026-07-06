"""Tests for the double-brace templating engine."""

import pytest

from lemontage.engine import template
from lemontage.engine.context import RunContext


def make_ctx(**kw):
    base = dict(vars={}, input={}, matrix={}, output_dir="out", pipeline_name="p")
    base.update(kw)
    return RunContext(**base)


def test_whole_value_reference_keeps_type():
    ctx = make_ctx(step_outputs={"t": {"segments": [{"start": 0}]}})
    out = template.resolve("{{ steps.t.segments }}", ctx)
    assert out == [{"start": 0}]


def test_interpolated_reference_is_stringified():
    ctx = make_ctx(vars={"topic": "deepseek"})
    assert template.resolve("about {{ vars.topic }}!", ctx) == "about deepseek!"


def test_input_and_matrix_namespaces():
    ctx = make_ctx(input={"source": "ep.mp4"}, matrix={"lang": "fr"})
    assert template.resolve("{{ input.source }}", ctx) == "ep.mp4"
    assert template.resolve("{{ matrix.lang }}", ctx) == "fr"


def test_nested_structures_are_resolved():
    ctx = make_ctx(vars={"a": 1})
    # Whole-value refs keep their type; interpolated refs are stringified.
    out = template.resolve({"x": ["{{ vars.a }}", "n={{ vars.a }}", "lit"]}, ctx)
    assert out == {"x": [1, "n=1", "lit"]}


def test_name_placeholder_resolves_to_pipeline_name():
    ctx = make_ctx(pipeline_name="ufc-highlights")
    assert template.resolve("./out/{{ name }}-{{ index }}.mp4", ctx) == (
        "./out/ufc-highlights-{{ index }}.mp4"
    )


def test_index_placeholder_is_left_for_export():
    # `index` is per-item; the resolver must not consume it.
    assert template.resolve("{{ index }}", make_ctx()) == "{{ index }}"


def test_part_placeholder_is_left_for_export():
    # `part` (1-based) is per-item too; resolved later by the export block.
    assert template.resolve("#{{ part }} / 3", make_ctx()) == "#{{ part }} / 3"


def test_unknown_namespace_raises():
    with pytest.raises(template.TemplateError):
        template.resolve("{{ bogus.x }}", make_ctx())


def test_unknown_step_raises():
    with pytest.raises(template.TemplateError):
        template.resolve("{{ steps.missing.text }}", make_ctx())


def test_missing_key_raises():
    ctx = make_ctx(vars={"a": {"b": 1}})
    with pytest.raises(template.TemplateError):
        template.resolve("{{ vars.a.c }}", ctx)


def test_steps_reference_without_id_raises():
    with pytest.raises(template.TemplateError, match="needs a step id"):
        template.resolve("{{ steps }}", make_ctx())


def test_walk_into_non_dict_raises():
    # Indexing into a list value ({{ steps.t.items.0 }}) is not supported.
    ctx = make_ctx(step_outputs={"t": {"items": [1, 2, 3]}})
    with pytest.raises(template.TemplateError, match="no key '0'"):
        template.resolve("{{ steps.t.items.0 }}", ctx)
