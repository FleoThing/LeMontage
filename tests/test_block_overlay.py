"""Tests for the `overlay` block (FFmpeg and fonts mocked)."""

from pathlib import Path

import pytest

from lemontage.engine import ffmpeg, fonts
from lemontage.engine.blocks.overlay import OverlayBlock
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


@pytest.fixture()
def calls(monkeypatch):
    calls = {}

    def fake_run(args):
        calls["args"] = args
        calls["vf"] = args[args.index("-vf") + 1]
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda _media: (1080, 1920))
    monkeypatch.setattr(fonts, "ensure", lambda _f: None)
    return calls


def ass_text(vf: str) -> str:
    path = vf.split("ass='", 1)[1].split("':fontsdir", 1)[0].replace("\\:", ":")
    return Path(path).read_text()


def test_overlay_single_mode_text_only(tmp_path, calls):
    out = OverlayBlock().execute({"text": "hello"}, ctx(tmp_path), "ov").outputs
    assert calls["vf"].startswith("ass=")
    assert "drawbox" not in calls["vf"]
    assert "hello" in ass_text(calls["vf"])
    assert out["clip"].endswith("ov.mp4")


def test_overlay_band_and_window(tmp_path, calls):
    params = {
        "text": "line one\nline two",
        "band": {"color": "white", "height": 210, "position": "top"},
        "show": {"from": 0, "to": "11s"},
    }
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert "drawbox=x=0:y=0:w=iw:h=210:color=white:t=fill" in calls["vf"]
    assert "enable='between(t,0,11)'" in calls["vf"]
    ass = ass_text(calls["vf"])
    assert r"line one\Nline two" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:11.00" in ass


def test_overlay_bottom_band_positions_from_frame_height(tmp_path, calls):
    params = {"text": "t", "band": {"height": 200, "position": "bottom"}}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert "y=1720" in calls["vf"]  # 1920 - 200


def test_overlay_mapped_mode_over_channel(tmp_path, calls):
    res = OverlayBlock().execute_item(
        {"text": "t"}, {"clip": "c.mp4", "index": 2}, ctx(tmp_path), "ov"
    )
    assert res.item["clip"].endswith("ov-2.mp4")
    assert res.outputs["clips"].endswith("ov-2.mp4")


def test_overlay_requires_text(tmp_path, calls):
    with pytest.raises(ValueError, match="text"):
        OverlayBlock().execute({}, ctx(tmp_path), "ov")


def test_overlay_rejects_bad_window(tmp_path, calls):
    with pytest.raises(ValueError, match="show.to"):
        params = {"text": "t", "show": {"from": "5s", "to": "2s"}}
        OverlayBlock().execute(params, ctx(tmp_path), "ov")


def test_overlay_rejects_show_except(tmp_path, calls):
    with pytest.raises(ValueError, match="except"):
        OverlayBlock().execute({"text": "t", "show": {"except": "transition"}}, ctx(tmp_path), "ov")


def test_overlay_rejects_bad_band_position(tmp_path, calls):
    with pytest.raises(ValueError, match="band.position"):
        OverlayBlock().execute({"text": "t", "band": {"position": "left"}}, ctx(tmp_path), "ov")


# --- validator ---------------------------------------------------------------


def pipeline(overlay_params):
    return {
        "lemontage": "1.0",
        "name": "demo",
        "input": {"type": "video", "source": "./in.mp4"},
        "steps": [
            {"id": "clips", "detect_clips": {"emit": "clip_channel"}},
            {"overlay": {"from": "clip_channel", **overlay_params}},
        ],
    }


def test_validator_accepts_full_overlay():
    doc = pipeline(
        {
            "text": "line one\nline two",
            "band": {"color": "white", "height": 210, "position": "top"},
            "show": {"from": 0, "to": "11s"},
        }
    )
    assert validate_doc(doc) == []


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({}, "non-empty 'text'"),
        ({"text": "t", "band": "white"}, "band must be a mapping"),
        ({"text": "t", "band": {"height": 0}}, "band.height"),
        ({"text": "t", "band": {"position": "left"}}, "band.position"),
        ({"text": "t", "show": "5s"}, "show must be a mapping"),
        ({"text": "t", "show": {"from": "nope"}}, "show.from"),
        ({"text": "t", "show": {"from": "5s", "to": "2s"}}, "show.to"),
        ({"text": "t", "show": {"except": "transition"}}, "not supported"),
    ],
)
def test_validator_rejects_bad_overlay(params, needle):
    errors = validate_doc(pipeline(params))
    assert any(needle in e for e in errors), errors
