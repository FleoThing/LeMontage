"""Tests for the DAG builder."""

import pytest

from reelflow.engine.dag import DagError, build_dag


def order(steps):
    return [n.step_id for n in build_dag(steps)]


def test_listed_order_preserved_when_independent():
    steps = [
        {"id": "a", "stt": {}},
        {"id": "b", "tts": {"text": "x"}},
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
