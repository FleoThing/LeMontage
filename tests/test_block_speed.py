"""Tests for the `speed` block (FFmpeg mocked)."""

from pathlib import Path

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.speed import SpeedBlock, _atempo_chain, _factor
from lemontage.engine.context import RunContext


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


def capture(monkeypatch):
    """Mock ffmpeg.run: record args and create the output file."""
    calls = {}

    def fake_run(args):
        calls["args"] = args
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    return calls


def test_factor_must_be_positive():
    with pytest.raises(ValueError):
        _factor({"factor": 0})


def test_atempo_chain_within_range_is_single_filter():
    assert _atempo_chain(1.5) == "atempo=1.500000"


def test_atempo_chain_above_two_is_chained():
    # 4x = 2.0 * 2.0
    assert _atempo_chain(4.0) == "atempo=2.0,atempo=2.000000"


def test_atempo_chain_below_half_is_chained():
    # 0.25x = 0.5 * 0.5
    assert _atempo_chain(0.25) == "atempo=0.5,atempo=0.500000"


def test_speed_single_mode_builds_setpts(tmp_path, monkeypatch):
    calls = capture(monkeypatch)
    out = SpeedBlock().execute({"factor": 2}, ctx(tmp_path), "spd").outputs
    args = calls["args"]
    assert "setpts=0.500000*PTS" in args
    assert "atempo=2.000000" in args
    assert out["clip"].endswith("spd.mp4")
    assert Path(out["clip"]).exists()


def test_speed_mapped_mode_over_channel(tmp_path, monkeypatch):
    capture(monkeypatch)
    item = {"clip": "c.mp4", "index": 3}
    res = SpeedBlock().execute_item({"factor": 0.5}, item, ctx(tmp_path), "spd")
    assert res.outputs["clips"].endswith("spd-3.mp4")


def test_speed_mapped_mode_requires_clip(tmp_path, monkeypatch):
    capture(monkeypatch)
    with pytest.raises(ValueError):
        SpeedBlock().execute_item({"factor": 2}, {"index": 0}, ctx(tmp_path), "spd")
