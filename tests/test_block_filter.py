"""Tests for the `filter` block (FFmpeg mocked)."""

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.filter import FilterBlock
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


@pytest.fixture
def captured(monkeypatch):
    calls = {}
    monkeypatch.setattr(ffmpeg, "run", lambda args: calls.setdefault("args", args))
    return calls


def chain_of(calls):
    args = calls["args"]
    return args[args.index("-vf") + 1]


def test_single_look_builds_chain(tmp_path, captured):
    FilterBlock().execute({"look": "bw"}, ctx(tmp_path), "f")
    assert chain_of(captured) == "hue=s=0"


def test_looks_compose_in_order_after_eq(tmp_path, captured):
    FilterBlock().execute(
        {"look": ["grain", "vignette"], "eq": {"contrast": 1.2, "saturation": 0.8}},
        ctx(tmp_path),
        "f",
    )
    chain = chain_of(captured)
    assert chain == "eq=contrast=1.2:saturation=0.8,noise=alls=12:allf=t,vignette=PI/5"


def test_maps_over_channel_item_updates_clip(tmp_path, captured):
    res = FilterBlock().execute_item(
        {"look": "sharpen"}, {"index": 2, "clip": "c.mp4"}, ctx(tmp_path), "f"
    )
    assert res.item["clip"].endswith("f-2.mp4")


def test_unknown_look_raises(tmp_path, captured):
    with pytest.raises(ValueError, match="unknown look"):
        FilterBlock().execute({"look": "sepia"}, ctx(tmp_path), "f")


def test_empty_filter_raises(tmp_path, captured):
    with pytest.raises(ValueError, match="nothing to do"):
        FilterBlock().execute({}, ctx(tmp_path), "f")


def _doc(params):
    return {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [
            {"id": "clips", "detect_clips": {"emit": "ch"}},
            {"filter": {"from": "ch", **params}},
        ],
    }


def test_validator_rejects_unknown_look_and_eq_key():
    assert any("unknown filter look" in e for e in validate_doc(_doc({"look": "toon"})))
    assert any("unknown filter.eq key" in e for e in validate_doc(_doc({"eq": {"hue": 1}})))


def test_validator_requires_something():
    assert any("needs a 'look'" in e for e in validate_doc(_doc({})))


def test_validator_accepts_valid_filter():
    assert validate_doc(_doc({"look": ["bw", "grain"], "eq": {"brightness": 0.1}})) == []
