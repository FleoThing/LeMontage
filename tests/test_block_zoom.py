"""Tests for the `zoom` block (FFmpeg mocked)."""

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.zoom import ZoomBlock, amount_for, punch_expr
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
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(ffmpeg, "probe_fps", lambda p: 24.0)
    return calls


def chain_of(calls):
    args = calls["args"]
    return args[args.index("-vf") + 1]


def test_static_punch_is_a_crop_and_scale(tmp_path, captured):
    """No `at` → one framing for the whole clip: exact, and no zoompan jitter."""
    ZoomBlock().execute({"amount": 1.2}, ctx(tmp_path), "z")
    assert chain_of(captured) == "crop=trunc(iw/1.2/2)*2:trunc(ih/1.2/2)*2,scale=1920:1080"


def test_punches_use_zoompan_at_the_source_fps(tmp_path, captured):
    """zoompan re-times to its `fps`, so it must get the source's — else the
    clip silently plays faster or slower than it was cut."""
    ZoomBlock().execute({"amount": 1.15, "at": [2, 5]}, ctx(tmp_path), "z")
    chain = chain_of(captured)
    assert chain.startswith("zoompan=z='1+(0.1500)*(")
    assert ":d=1:s=1920x1080:fps=24.0" in chain


def test_punch_expr_alternates_in_and_out():
    expr = punch_expr(1.2, [1.0, 3.0, 6.0], ramp=0.15, fps=30)
    assert expr.count("+(0.2000)") == 2  # in at 1s and 6s
    assert expr.count("+(-0.2000)") == 1  # out at 3s
    assert "min(1,max(0,(on/30.000-3.000)/0.150))" in expr


def test_amount_list_frames_each_clip_differently():
    params = {"amount": [1.0, 1.2]}
    assert amount_for(params, 0) == 1.0
    assert amount_for(params, 1) == 1.2
    assert amount_for(params, 2) == 1.0  # past the end → no punch, not an error


def test_mapped_zoom_punches_the_exported_file_when_there_is_one(tmp_path, captured):
    item = {"index": 0, "clip": "cut-0.mp4", "file": "exported-0.mp4"}
    result = ZoomBlock().execute_item({"amount": 1.1}, item, ctx(tmp_path), "z")
    assert captured["args"][captured["args"].index("-i") + 1] == "exported-0.mp4"
    assert "file" in result.item


def test_mapped_zoom_needs_a_cut_clip(tmp_path, captured):
    with pytest.raises(ValueError, match="run 'cut' first"):
        ZoomBlock().execute_item({}, {"index": 0}, ctx(tmp_path), "z")


def doc(params):
    return {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [{"zoom": params}],
    }


def test_validator_accepts_a_punch_list():
    assert validate_doc(doc({"amount": [1.0, 1.15], "at": [1, "0:02"]})) == []


def test_validator_rejects_a_zoom_out():
    assert any(
        "zoom.amount must be a number >= 1.0" in e for e in validate_doc(doc({"amount": 0.8}))
    )


def test_validator_rejects_a_bad_at():
    assert any("is not a time" in e for e in validate_doc(doc({"at": ["soon"]})))


def test_validator_rejects_a_zero_duration():
    assert any("zoom.duration must be > 0" in e for e in validate_doc(doc({"duration": 0})))
