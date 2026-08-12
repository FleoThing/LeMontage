"""Tests for the DAG builder."""

import pytest

from lemontage.engine.dag import DagError, build_dag


def order(steps):
    return [n.step_id for n in build_dag(steps)]


def test_listed_order_preserved_when_independent():
    steps = [
        {"id": "a", "stt": {}},
        {"id": "b", "export": {"format": "vertical"}},
    ]
    assert order(steps) == ["a", "b"]


def test_template_dependency_orders_steps():
    # 'b' references 'a' even though listed first -> a must come before b.
    steps = [
        {"id": "b", "captions": {"segments": "{{ steps.a.segments }}"}},
        {"id": "a", "stt": {}},
    ]
    assert order(steps).index("a") < order(steps).index("b")


def test_channel_producer_before_consumers_in_listed_order():
    steps = [
        {"id": "clips", "detect_clips": {"emit": "ch"}},
        {"id": "cut", "cut": {"from": "ch"}},
        {"id": "cap", "captions": {"from": "ch"}},
        {"id": "exp", "export": {"from": "ch"}},
    ]
    result = order(steps)
    assert result == ["clips", "cut", "cap", "exp"]


def test_unknown_channel_raises():
    steps = [{"id": "cut", "cut": {"from": "nope"}}]
    with pytest.raises(DagError):
        build_dag(steps)


def test_unknown_step_reference_raises():
    steps = [{"id": "a", "captions": {"segments": "{{ steps.ghost.segments }}"}}]
    with pytest.raises(DagError):
        build_dag(steps)


def test_emit_and_consume_recorded():
    steps = [
        {"id": "clips", "detect_clips": {"emit": "ch"}},
        {"id": "cut", "cut": {"from": "ch"}},
    ]
    nodes = {n.step_id: n for n in build_dag(steps)}
    assert nodes["clips"].emits == "ch"
    assert nodes["cut"].consumes == "ch"
    assert nodes["cut"].consumes_list == ["ch"]


# --- multi-channel `from` (channel operators) -------------------------------


def test_merge_step_depends_on_all_listed_producers():
    steps = [
        {"id": "va", "detect_clips": {"method": "loudness", "emit": "viral"}},
        {"id": "mo", "detect_clips": {"method": "random", "emit": "montage"}},
        {"id": "reel", "concat": {"from": ["viral", "montage"]}},
    ]
    result = order(steps)
    assert result.index("va") < result.index("reel")
    assert result.index("mo") < result.index("reel")
    nodes = {n.step_id: n for n in build_dag(steps)}
    assert nodes["reel"].consumes_list == ["viral", "montage"]
    assert nodes["reel"].consumes is None  # multi-channel -> no single consumes


def test_merge_runs_after_each_channels_last_consumer():
    # concat must run after BOTH export steps (it prefers the exported file).
    steps = [
        {"id": "va", "detect_clips": {"method": "loudness", "emit": "viral"}},
        {"id": "eva", "export": {"from": "viral"}},
        {"id": "mo", "detect_clips": {"method": "silence", "emit": "montage"}},
        {"id": "emo", "export": {"from": "montage"}},
        {"id": "reel", "concat": {"from": ["viral", "montage"]}},
    ]
    result = order(steps)
    assert result.index("eva") < result.index("reel")
    assert result.index("emo") < result.index("reel")


def test_unknown_channel_in_list_raises():
    steps = [
        {"id": "va", "detect_clips": {"emit": "viral"}},
        {"id": "reel", "concat": {"from": ["viral", "ghost"]}},
    ]
    with pytest.raises(DagError, match="ghost"):
        build_dag(steps)


# -- `requires` is a dependency (dag._add_requires_edges) ----------------------


def test_requires_orders_the_step_after_its_gate():
    """A step that requires one written *below* it used to run first, and skip.

    `requires` is a plain string, not a `{{ steps.… }}` reference, so it created
    no edge: ordering came from declaration order alone. Declared above its gate,
    the step read `pending` instead of `success` and skipped itself every run.
    """
    nodes = build_dag(
        [
            {"id": "b", "export": {}, "requires": "a.success"},
            {"id": "a", "stt": {}},
        ]
    )
    assert [n.step_id for n in nodes] == ["a", "b"]


def test_requires_edge_survives_the_usual_order():
    nodes = build_dag(
        [
            {"id": "a", "stt": {}},
            {"id": "b", "export": {}, "requires": "a.success"},
        ]
    )
    by_id = {n.step_id: n for n in nodes}
    assert by_id["a"].index in by_id["b"].deps


def test_requires_on_an_unknown_step_is_left_alone():
    """A typo still means "never matches, so skip" -- not a hard error."""
    nodes = build_dag([{"id": "b", "export": {}, "requires": "nope.success"}])
    assert [n.step_id for n in nodes] == ["b"]
    assert nodes[0].deps == set()


def test_requires_cannot_depend_on_itself():
    nodes = build_dag([{"id": "a", "stt": {}, "requires": "a.success"}])
    assert nodes[0].deps == set()


def test_requires_cycle_is_reported():
    with pytest.raises(DagError, match="cycle"):
        build_dag(
            [
                {"id": "a", "stt": {}, "requires": "b.success"},
                {"id": "b", "export": {}, "requires": "a.success"},
            ]
        )
