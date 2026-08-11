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
        # an `image` overlay needs a second input, so it renders via filter_complex
        key = "-filter_complex" if "-filter_complex" in args else "-vf"
        calls["vf"] = args[args.index(key) + 1]
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda _media: (1080, 1920))
    monkeypatch.setattr(fonts, "ensure", lambda _f: None)
    return calls


@pytest.fixture()
def png(tmp_path):
    path = tmp_path / "card.png"
    path.write_bytes(b"\x89PNG")
    return str(path)


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


def test_overlay_requires_text_or_image(tmp_path, calls):
    with pytest.raises(ValueError, match="'text' and/or an 'image'"):
        OverlayBlock().execute({}, ctx(tmp_path), "ov")


# --- image ---------------------------------------------------------------------


def test_overlay_image_alone_composites_at_origin(tmp_path, calls, png):
    OverlayBlock().execute({"image": png}, ctx(tmp_path), "ov")
    assert calls["vf"] == "[0:v][1:v]overlay=0:0[composited]"
    assert calls["args"][calls["args"].index("-map") + 1] == "[composited]"
    assert "0:a?" in calls["args"]  # audio survives, and a silent clip still works
    assert png in calls["args"]
    assert "ass=" not in calls["vf"]  # no text asked for, no libass pass


def test_overlay_image_at_pixel_position(tmp_path, calls, png):
    OverlayBlock().execute({"image": png, "x": 0, "y": 421}, ctx(tmp_path), "ov")
    assert "overlay=0:421" in calls["vf"]


def test_overlay_image_negative_xy_counts_from_far_edge(tmp_path, calls, png):
    OverlayBlock().execute({"image": png, "x": -40, "y": -30}, ctx(tmp_path), "ov")
    assert "overlay=W-w-40:H-h-30" in calls["vf"]


def test_overlay_image_gated_by_show_window(tmp_path, calls, png):
    params = {"image": png, "show": {"from": "2s", "to": "6s"}}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert "overlay=0:0:enable='between(t,2,6)'" in calls["vf"]


def test_overlay_image_under_text_and_over_band(tmp_path, calls, png):
    params = {"text": "hi", "image": png, "band": {"height": 200, "position": "top"}}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    graph = calls["vf"]
    # band first, image on the banded frame, text last so it stays readable
    assert graph.startswith("[0:v]drawbox=")
    assert "[banded][1:v]overlay=" in graph
    assert "[composited]ass=" in graph
    assert graph.endswith("[out]")


def test_overlay_missing_image_raises(tmp_path, calls):
    with pytest.raises(ValueError, match="not found"):
        OverlayBlock().execute({"image": str(tmp_path / "nope.png")}, ctx(tmp_path), "ov")


def test_overlay_non_integer_xy_raises(tmp_path, calls, png):
    with pytest.raises(ValueError, match="x must be an integer"):
        OverlayBlock().execute({"image": png, "x": "12px"}, ctx(tmp_path), "ov")


# --- coloured runs -------------------------------------------------------------


def test_overlay_runs_colour_each_phrase(tmp_path, calls):
    params = {
        "text": [
            {"text": "Apollo 11's", "color": "#FFFF00"},
            {"text": " flag never stayed "},
            {"text": "standing", "color": "red"},
        ]
    }
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    line = [ln for ln in ass_text(calls["vf"]).splitlines() if ln.startswith("Dialogue:")][0]
    # yellow is &H0000FFFF in ASS's BBGGRR order; \r returns to the style default
    assert r"{\c&H0000FFFF&}Apollo 11's{\r} flag never stayed {\c&H000000FF&}standing{\r}" in line


def test_overlay_runs_keep_their_own_spacing(tmp_path, calls):
    params = {"text": [{"text": "a"}, {"text": " b"}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert "a b" in ass_text(calls["vf"])


def test_overlay_run_without_colour_is_plain(tmp_path, calls):
    OverlayBlock().execute({"text": [{"text": "plain"}]}, ctx(tmp_path), "ov")
    ass = ass_text(calls["vf"])
    assert "plain" in ass and r"\c&H" not in ass


def test_overlay_runs_still_escape_user_text(tmp_path, calls):
    """The colour tags are ours; the run's own braces must stay neutralised."""
    params = {"text": [{"text": r"{\fscx300}pwned", "color": "white"}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    line = [ln for ln in ass_text(calls["vf"]).splitlines() if ln.startswith("Dialogue:")][0]
    assert "fscx300" in line  # kept as literal text...
    assert r"{\fscx300}" not in line  # ...but never as an override block
    assert line.count("{") == 2 and line.count("}") == 2  # only our \c and \r


def test_overlay_runs_multiline_centre_in_band(tmp_path, calls):
    params = {"text": [{"text": "one\ntwo"}], "band": {"height": 300}, "size": 100}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    ass = ass_text(calls["vf"])
    assert r"one\Ntwo" in ass
    assert ",50,1" in ass  # (300 - 100*2) // 2 = 50, so both lines are counted


def test_overlay_run_bad_colour_names_the_run(tmp_path, calls):
    params = {"text": [{"text": "a"}, {"text": "b", "color": "fuschia"}]}
    with pytest.raises(ValueError, match=r"overlay.text\[1\].color"):
        OverlayBlock().execute(params, ctx(tmp_path), "ov")


def test_overlay_empty_run_rejected(tmp_path, calls):
    with pytest.raises(ValueError, match="text run 0"):
        OverlayBlock().execute({"text": [{"color": "red"}]}, ctx(tmp_path), "ov")


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


def test_validator_accepts_image_only_overlay():
    assert validate_doc(pipeline({"image": "./card.png", "x": 0, "y": 421})) == []


def test_validator_accepts_coloured_runs():
    doc = pipeline({"text": [{"text": "a", "color": "yellow"}, {"text": " b"}]})
    assert validate_doc(doc) == []


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({}, "'text' and/or an 'image'"),
        ({"image": 12}, "overlay.image must be a path"),
        ({"image": "./c.png", "y": "421px"}, "overlay.y must be an integer"),
        ({"text": []}, "list of {text, color} runs"),
        ({"text": ["a", "b"]}, "list of {text, color} runs"),
        ({"text": [{"color": "red"}]}, "list of {text, color} runs"),
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
