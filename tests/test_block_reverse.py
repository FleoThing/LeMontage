"""Tests for the `reverse` block (FFmpeg mocked)."""

from pathlib import Path

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.reverse import ReverseBlock
from lemontage.engine.context import RunContext


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


def capture(monkeypatch):
    calls = {}

    def fake_run(args):
        calls["args"] = args
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    return calls


def test_reverse_single_mode_uses_reverse_filters(tmp_path, monkeypatch):
    calls = capture(monkeypatch)
    out = ReverseBlock().execute({}, ctx(tmp_path), "rev").outputs
    assert "reverse" in calls["args"]
    assert "areverse" in calls["args"]
    assert out["clip"].endswith("rev.mp4")
    assert Path(out["clip"]).exists()


def test_reverse_mapped_mode_over_channel(tmp_path, monkeypatch):
    capture(monkeypatch)
    res = ReverseBlock().execute_item({}, {"clip": "c.mp4", "index": 2}, ctx(tmp_path), "rev")
    assert res.outputs["clips"].endswith("rev-2.mp4")
    assert res.item["clip"].endswith("rev-2.mp4")


def test_reverse_mapped_mode_requires_clip(tmp_path, monkeypatch):
    capture(monkeypatch)
    with pytest.raises(ValueError):
        ReverseBlock().execute_item({}, {"index": 0}, ctx(tmp_path), "rev")


def test_reverse_single_mode_requires_media(tmp_path, monkeypatch):
    capture(monkeypatch)
    bad = RunContext(vars={}, input={}, matrix={}, output_dir=tmp_path, pipeline_name="d")
    with pytest.raises(ValueError):
        ReverseBlock().execute({}, bad, "rev")
