"""Tests for the executor: states, requires, on_failure, channels, matrix, cache.

Blocks are replaced with lightweight fakes so the executor logic is exercised
without FFmpeg or any model.
"""

import pytest

from lemontage.engine import executor
from lemontage.engine.blocks.base import Block, BlockResult, ItemResult


class RecordingBlock(Block):
    """A single-mode block that records how many times it ran."""

    def __init__(self, name, fail_times=0):
        self.name = name
        self.calls = 0
        self._fail_times = fail_times

    def execute(self, params, ctx, step_id):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("boom")
        return BlockResult(outputs={"ran": self.calls, "value": params.get("value")})


class Producer(Block):
    name = "detect_clips"

    def execute(self, params, ctx, step_id):
        items = [{"index": i, "start": i, "end": i + 1} for i in range(params.get("n", 2))]
        return BlockResult(outputs={"count": len(items)}, channel_items=items)


class Mapper(Block):
    """A mapped block that tags each item and emits a per-item output."""

    def __init__(self, name):
        self.name = name
        self.item_calls = 0

    def execute(self, params, ctx, step_id):  # pragma: no cover - not used in single mode
        raise AssertionError("mapper should run mapped")

    def execute_item(self, params, item, ctx, step_id):
        self.item_calls += 1
        return ItemResult(
            item={"clip": f"clip-{item['index']}"},
            outputs={"files": f"out-{item['index']}.mp4"},
        )


@pytest.fixture
def patch_registry(monkeypatch):
    registry = {}
    monkeypatch.setattr(executor, "REGISTRY", registry)
    return registry


def run(doc, **kw):
    return executor.run_pipeline(doc, **kw)


def base_doc(steps, **kw):
    doc = {"lemontage": "1.0", "name": "t", "input": {"source": "x.mp4"}, "steps": steps}
    doc.update(kw)
    return doc


def test_simple_success(patch_registry, tmp_path):
    patch_registry["stt"] = RecordingBlock("stt")
    doc = base_doc([{"id": "a", "stt": {"value": 7}}], output={"dir": str(tmp_path)})
    result = run(doc, reporter=lambda m: None)
    assert result.ok
    cell = result.cells[0]
    assert cell.states["a"] == executor.SUCCESS
    assert cell.outputs["a"]["value"] == 7


def test_var_override_flows_into_params(patch_registry, tmp_path):
    block = RecordingBlock("stt")
    patch_registry["stt"] = block
    doc = base_doc(
        [{"id": "a", "stt": {"value": "{{ vars.v }}"}}],
        vars={"v": "default"},
        output={"dir": str(tmp_path)},
    )
    result = run(doc, var_overrides={"v": "override"}, reporter=lambda m: None)
    assert result.cells[0].outputs["a"]["value"] == "override"


def test_on_failure_abort_stops_pipeline(patch_registry, tmp_path):
    patch_registry["stt"] = RecordingBlock("stt", fail_times=1)
    patch_registry["export"] = RecordingBlock("export")
    doc = base_doc(
        [
            {"id": "a", "stt": {}, "on_failure": "abort"},
            {"id": "b", "export": {}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert not result.ok
    assert result.cells[0].states["a"] == executor.FAILED
    # 'b' never reached -> stays pending.
    assert result.cells[0].states["b"] == executor.PENDING


def test_on_failure_skip_continues(patch_registry, tmp_path):
    patch_registry["stt"] = RecordingBlock("stt", fail_times=1)
    patch_registry["export"] = RecordingBlock("export")
    doc = base_doc(
        [
            {"id": "a", "stt": {}, "on_failure": "skip"},
            {"id": "b", "export": {}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.ok
    assert result.cells[0].states["a"] == executor.SKIPPED
    assert result.cells[0].states["b"] == executor.SUCCESS


def test_on_failure_retry_eventually_succeeds(patch_registry, tmp_path):
    block = RecordingBlock("stt", fail_times=2)
    patch_registry["stt"] = block
    doc = base_doc(
        [{"id": "a", "stt": {}, "on_failure": "retry", "retries": 2}],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.ok
    assert block.calls == 3  # 2 failures + 1 success


def test_requires_gate_skips_when_unmet(patch_registry, tmp_path):
    patch_registry["stt"] = RecordingBlock("stt", fail_times=1)
    patch_registry["export"] = RecordingBlock("export")
    doc = base_doc(
        [
            {"id": "a", "stt": {}, "on_failure": "skip"},
            {"id": "b", "export": {}, "requires": "a.success"},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    # 'a' was skipped (not success) -> 'b' requires unmet -> skipped.
    assert result.cells[0].states["b"] == executor.SKIPPED


def test_channel_fan_out_runs_once_per_item(patch_registry, tmp_path):
    patch_registry["detect_clips"] = Producer()
    mapper = Mapper("export")
    patch_registry["export"] = mapper
    doc = base_doc(
        [
            {"id": "clips", "detect_clips": {"n": 3, "emit": "ch"}},
            {"id": "exp", "export": {"from": "ch"}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert mapper.item_calls == 3
    assert sorted(result.cells[0].outputs["exp"]["files"]) == [
        "out-0.mp4",
        "out-1.mp4",
        "out-2.mp4",
    ]


def test_channel_items_chain_through_consumers(patch_registry, tmp_path):
    patch_registry["detect_clips"] = Producer()
    patch_registry["cut"] = Mapper("cut")

    seen = []

    class Reader(Mapper):
        def execute_item(self, params, item, ctx, step_id):
            seen.append(item.get("clip"))
            return ItemResult(item={}, outputs={"files": item.get("clip")})

    patch_registry["export"] = Reader("export")
    doc = base_doc(
        [
            {"id": "clips", "detect_clips": {"n": 2, "emit": "ch"}},
            {"id": "cut", "cut": {"from": "ch"}},
            {"id": "exp", "export": {"from": "ch"}},
        ],
        output={"dir": str(tmp_path)},
    )
    run(doc, reporter=lambda m: None)
    # export saw the 'clip' field that cut wrote onto each item.
    assert sorted(seen) == ["clip-0", "clip-1"]


def test_channel_aggregator_receives_all_items(patch_registry, tmp_path):
    patch_registry["detect_clips"] = Producer()

    class Aggregator(Block):
        name = "concat"
        maps = False

        def execute(self, params, ctx, step_id):  # pragma: no cover
            raise AssertionError("should aggregate, not run single")

        def execute_channel(self, params, items, ctx, step_id):
            return BlockResult(outputs={"count": len(items)})

    patch_registry["concat"] = Aggregator()
    doc = base_doc(
        [
            {"id": "clips", "detect_clips": {"n": 3, "emit": "ch"}},
            {"id": "reel", "concat": {"from": "ch"}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.cells[0].outputs["reel"]["count"] == 3


class RecordingAggregator(Block):
    """A concat-like aggregator that records the items it received."""

    name = "concat"
    maps = False

    def __init__(self):
        self.items = None

    def execute(self, params, ctx, step_id):  # pragma: no cover - must aggregate
        raise AssertionError("should aggregate, not run single")

    def execute_channel(self, params, items, ctx, step_id):
        self.items = items
        return BlockResult(outputs={"count": len(items)})


def test_merge_channels_orders_in_listed_order_and_reindexes(patch_registry, tmp_path):
    patch_registry["detect_clips"] = Producer()
    agg = RecordingAggregator()
    patch_registry["concat"] = agg
    doc = base_doc(
        [
            {"id": "va", "detect_clips": {"n": 1, "emit": "viral"}},
            {"id": "mo", "detect_clips": {"n": 3, "emit": "montage"}},
            {"id": "reel", "concat": {"from": ["viral", "montage"]}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.cells[0].outputs["reel"]["count"] == 4
    # viral (1 item) then montage (3 items); Producer sets start=i per channel.
    assert [it["start"] for it in agg.items] == [0, 0, 1, 2]
    # merged items are re-indexed sequentially so a sort-by-index keeps this order.
    assert [it["index"] for it in agg.items] == [0, 1, 2, 3]


class NestingConcat(Block):
    """A concat-like aggregator that collapses its input to one reel and emits it."""

    name = "concat"
    maps = False

    def __init__(self):
        self.seen = {}  # step_id -> the files it received

    def execute(self, params, ctx, step_id):  # pragma: no cover - must aggregate
        raise AssertionError("should aggregate, not run single")

    def execute_channel(self, params, items, ctx, step_id):
        self.seen[step_id] = [it.get("file") for it in items]
        reel = f"{step_id}.mp4"
        return BlockResult(
            outputs={"file": reel},
            channel_items=[{"index": 0, "file": reel}],
        )


def test_nested_concat_reels_become_items_of_final_concat(patch_registry, tmp_path):
    """Each branch concats into one reel, emitted as a channel the final concat joins."""
    patch_registry["detect_clips"] = Producer()
    concat = NestingConcat()
    patch_registry["concat"] = concat
    doc = base_doc(
        [
            {"id": "ca", "detect_clips": {"n": 3, "emit": "a_clips"}},
            {"id": "ra", "concat": {"from": "a_clips", "emit": "reelA"}},
            {"id": "cb", "detect_clips": {"n": 2, "emit": "b_clips"}},
            {"id": "rb", "concat": {"from": "b_clips", "emit": "reelB"}},
            {"id": "final", "concat": {"from": ["reelA", "reelB"]}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.ok
    # The final concat saw exactly the two finished reels — one item per branch.
    assert concat.seen["final"] == ["ra.mp4", "rb.mp4"]


def test_merge_skips_empty_channel(patch_registry, tmp_path):
    patch_registry["detect_clips"] = Producer()
    agg = RecordingAggregator()
    patch_registry["concat"] = agg
    doc = base_doc(
        [
            {"id": "va", "detect_clips": {"n": 0, "emit": "viral"}},
            {"id": "mo", "detect_clips": {"n": 2, "emit": "montage"}},
            {"id": "reel", "concat": {"from": ["viral", "montage"]}},
        ],
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert result.cells[0].outputs["reel"]["count"] == 2  # empty 'viral' drops out


def test_matrix_fans_out_runs(patch_registry, tmp_path):
    block = RecordingBlock("stt")
    patch_registry["stt"] = block
    doc = base_doc(
        [{"id": "a", "stt": {"value": "{{ matrix.lang }}"}}],
        matrix={"lang": ["fr", "en"], "fmt": ["v", "s"]},
        output={"dir": str(tmp_path)},
    )
    result = run(doc, reporter=lambda m: None)
    assert len(result.cells) == 4
    langs = {c.outputs["a"]["value"] for c in result.cells}
    assert langs == {"fr", "en"}


def test_cache_skips_on_second_run(patch_registry, tmp_path):
    block = RecordingBlock("stt")
    patch_registry["stt"] = block
    doc = base_doc([{"id": "a", "stt": {"value": 1}}], output={"dir": str(tmp_path)})
    run(doc, reporter=lambda m: None)
    run(doc, reporter=lambda m: None)
    assert block.calls == 1  # second run served from cache


def test_cached_step_satisfies_requires_on_rerun(patch_registry, tmp_path):
    """A cache hit counts as success, so a downstream `requires` still passes."""
    patch_registry["stt"] = RecordingBlock("stt")
    patch_registry["export"] = RecordingBlock("export")
    doc = base_doc(
        [
            {"id": "a", "stt": {}},
            {"id": "b", "export": {}, "requires": "a.success"},
        ],
        output={"dir": str(tmp_path)},
    )
    run(doc, reporter=lambda m: None)  # warms the cache for 'a'
    result = run(doc, reporter=lambda m: None)  # 'a' now cached
    assert result.cells[0].states["a"] == executor.SUCCESS
    assert result.cells[0].states["b"] == executor.SUCCESS


def test_cache_disabled_reruns(patch_registry, tmp_path):
    block = RecordingBlock("stt")
    patch_registry["stt"] = block
    doc = base_doc(
        [{"id": "a", "stt": {"value": 1}, "cache": False}], output={"dir": str(tmp_path)}
    )
    run(doc, reporter=lambda m: None)
    run(doc, reporter=lambda m: None)
    assert block.calls == 2
