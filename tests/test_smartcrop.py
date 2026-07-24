"""Tests for subject-following `export: smart_crop` (mediapipe/OpenCV mocked)."""

import pytest

from lemontage.engine import ffmpeg, smartcrop
from lemontage.engine.blocks.export import ExportBlock
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


def test_crop_filters_pans_following_subject(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(smartcrop, "_track_subject", lambda m: [(0.0, 0.5), (0.5, 0.9)])
    cmd = tmp_path / "c.cmd"

    filters = smartcrop.crop_filters("in.mp4", 1080, 1920, cmd)

    assert filters[0] == "scale=3414:1920"  # height-matched, width overflows
    assert filters[1].startswith("sendcmd=f=")
    assert filters[2].startswith("crop=1080:1920:")  # initial x = first sample
    lines = cmd.read_text().splitlines()
    assert lines[0].startswith("0.000 crop x ")
    # A subject further right (0.9) pans the crop window right, clamped to max.
    assert int(lines[1].split()[-1].rstrip(";")) > int(lines[0].split()[-1].rstrip(";"))


def test_crop_filters_static_when_source_not_wider(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1080, 1920))
    called = []
    monkeypatch.setattr(smartcrop, "_track_subject", lambda m: called.append(1) or [])
    filters = smartcrop.crop_filters("in.mp4", 1080, 1920, tmp_path / "c.cmd")
    assert "force_original_aspect_ratio=increase" in filters[0]
    assert not called  # no tracking work when there's nothing to pan


def test_export_smart_crop_builds_subject_chain(tmp_path, monkeypatch):
    args = {}
    monkeypatch.setattr(ffmpeg, "run", lambda a: args.setdefault("a", a))
    monkeypatch.setattr(
        smartcrop,
        "crop_filters",
        lambda m, w, h, c: ["scale=3414:1920", "sendcmd=f=x", "crop=1080:1920:100:0"],
    )
    ExportBlock().execute(
        {"smart_crop": True, "resolution": "1080x1920"}, ctx(tmp_path), "export"
    )
    vf = args["a"][args["a"].index("-vf") + 1]
    assert "sendcmd=f=x" in vf and "crop=1080:1920:100:0" in vf and vf.endswith("fps=30")


def test_validator_rejects_non_bool_smart_crop():
    doc = {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [{"export": {"smart_crop": "yes"}}],
    }
    assert any("smart_crop must be a boolean" in e for e in validate_doc(doc))


def test_missing_extra_raises_helpful_error(monkeypatch):
    """Without mediapipe installed, _track_subject points at the extra."""
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda p: 1.0)
    # mediapipe isn't installed in CI, so the real import fails.
    with pytest.raises(ValueError, match="lemontage\\[smartcrop\\]"):
        smartcrop._track_subject("in.mp4")
