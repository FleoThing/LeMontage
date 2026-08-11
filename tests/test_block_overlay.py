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
    with pytest.raises(ValueError, match=r"needs a 'text' \(or 'cues'\) and/or an 'image'"):
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


# --- placement ---------------------------------------------------------------


def style(vf: str) -> str:
    return [ln for ln in ass_text(vf).splitlines() if ln.startswith("Style:")][0]


def dialogues(vf: str) -> list[str]:
    return [ln for ln in ass_text(vf).splitlines() if ln.startswith("Dialogue:")]


@pytest.mark.parametrize(
    ("position", "align"),
    [("top-left", 7), ("top-center", 8), ("center", 5), ("bottom-left", 1), ("bottom-right", 3)],
)
def test_overlay_position_sets_alignment(tmp_path, calls, position, align):
    OverlayBlock().execute({"text": "t", "position": position}, ctx(tmp_path), "ov")
    assert style(calls["vf"]).endswith(f",{align},40,40,60,1")


def test_overlay_without_position_stays_top_centred(tmp_path, calls):
    """The historical default: no band, no position — top centre, 60px down."""
    OverlayBlock().execute({"text": "t"}, ctx(tmp_path), "ov")
    assert style(calls["vf"]).endswith(",8,40,40,60,1")


def test_overlay_band_still_drives_alignment(tmp_path, calls):
    OverlayBlock().execute({"text": "t", "band": {"position": "bottom"}}, ctx(tmp_path), "ov")
    assert ",2," in style(calls["vf"])  # bottom band -> bottom-centred text, as before


def test_overlay_position_overrides_the_band(tmp_path, calls):
    params = {"text": "t", "band": {"position": "top"}, "position": "top-right"}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert ",9," in style(calls["vf"])


def test_overlay_margin_x_sets_side_margins(tmp_path, calls):
    params = {"text": "t", "position": "bottom-left", "margin_x": 62, "margin": 440}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert style(calls["vf"]).endswith(",1,62,62,440,1")


def test_overlay_outline_defaults_to_none(tmp_path, calls):
    """Unchanged from before: flat text, right over a band or a card."""
    OverlayBlock().execute({"text": "t"}, ctx(tmp_path), "ov")
    assert ",1,0,0,8," in style(calls["vf"])  # BorderStyle, Outline, Shadow, Alignment


def test_overlay_outline_thickens_the_contour(tmp_path, calls):
    params = {"text": "t", "outline": 5, "outline_color": "#101010"}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert ",1,5,0,8," in style(calls["vf"])
    assert "&H00101010" in style(calls["vf"])


def test_overlay_rejects_negative_outline(tmp_path, calls):
    with pytest.raises(ValueError, match="outline must be a number"):
        OverlayBlock().execute({"text": "t", "outline": -2}, ctx(tmp_path), "ov")


def test_overlay_rejects_unknown_position(tmp_path, calls):
    with pytest.raises(ValueError, match="unknown position 'sideways'"):
        OverlayBlock().execute({"text": "t", "position": "sideways"}, ctx(tmp_path), "ov")


# --- run size and font ---------------------------------------------------------


def test_overlay_run_size_mixes_scales_on_one_line(tmp_path, calls):
    """A big rank number then a small label — one text block, two sizes."""
    params = {"text": [{"text": "1.", "size": 92}, {"text": "  Jumpscare", "size": 40}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert r"{\fs92}1.{\r}{\fs40}  Jumpscare{\r}" in dialogues(calls["vf"])[0]


def test_overlay_run_font_resolves_the_preset_alias(tmp_path, calls):
    OverlayBlock().execute({"text": [{"text": "a", "font": "font3"}]}, ctx(tmp_path), "ov")
    assert r"{\fnBangers}" in dialogues(calls["vf"])[0]


def test_overlay_run_combines_colour_size_and_font(tmp_path, calls):
    params = {"text": [{"text": "a", "color": "red", "size": 50, "font": "font2"}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert r"{\c&H000000FF&\fs50\fnBebas Neue}a{\r}" in dialogues(calls["vf"])[0]


def test_overlay_run_font_cannot_inject_ass(tmp_path, calls):
    """`\\fn` takes the family verbatim, so a brace in it would end the block."""
    params = {"text": [{"text": "a", "font": "Evil}{\\fscx300"}]}
    with pytest.raises(ValueError, match="cannot contain"):
        OverlayBlock().execute(params, ctx(tmp_path), "ov")


def test_overlay_run_bad_size_names_the_run(tmp_path, calls):
    with pytest.raises(ValueError, match=r"overlay.text\[0\].size"):
        OverlayBlock().execute({"text": [{"text": "a", "size": 0}]}, ctx(tmp_path), "ov")


# --- cues ----------------------------------------------------------------------


def test_overlay_cues_render_in_a_single_pass(tmp_path, calls):
    params = {
        "cues": [
            {"text": "first", "show": {"from": 0, "to": 6}},
            {"text": "second", "show": {"from": 6, "to": 12}},
            {"text": "third", "show": {"from": 12}},
        ]
    }
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert calls["vf"].count("ass=") == 1  # one libass filter, so one re-encode
    lines = dialogues(calls["vf"])
    assert len(lines) == 3
    assert "0:00:00.00,0:00:06.00" in lines[0]
    assert "0:00:06.00,0:00:12.00" in lines[1]
    assert "0:00:12.00,9:59:59.99" in lines[2]  # no `to` — holds to the last frame


def test_overlay_cues_are_pinned_not_flowed(tmp_path, calls):
    """libass shifts events that would overlap — a fixed column must not move.

    Two cues 102px apart at a size whose line boxes collide: without `\\pos`
    they get pushed off their seats, and the column's spacing collapses.
    """
    params = {
        "cues": [
            {"text": "1.", "position": "top-left", "size": 158, "margin": 440, "margin_x": 62},
            {"text": "2.", "position": "top-left", "size": 158, "margin": 542, "margin_x": 62},
        ]
    }
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    lines = dialogues(calls["vf"])
    assert r"{\pos(62,440)}1." in lines[0]
    assert r"{\pos(62,542)}2." in lines[1]


@pytest.mark.parametrize(
    ("position", "margin", "point"),
    [
        ("top-left", 440, "(62,440)"),
        ("top-center", 186, "(540,186)"),
        ("top-right", 186, "(1018,186)"),
        ("center", 0, "(540,960)"),
        ("bottom-center", 178, "(540,1742)"),
        ("bottom-right", 178, "(1018,1742)"),
    ],
)
def test_overlay_cue_anchor_matches_its_position(tmp_path, calls, position, margin, point):
    """The pinned point is what the anchor already meant, so margins don't move."""
    params = {"cues": [{"text": "x", "position": position, "margin": margin, "margin_x": 62}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert rf"{{\pos{point}}}" in dialogues(calls["vf"])[0]


def test_overlay_single_text_is_not_pinned(tmp_path, calls):
    """A lone `text` keeps flowing from its margins, exactly as it always has."""
    OverlayBlock().execute({"text": "t", "position": "top-left"}, ctx(tmp_path), "ov")
    assert r"\pos(" not in dialogues(calls["vf"])[0]


def test_overlay_cues_style_independently(tmp_path, calls):
    params = {
        "size": 40,
        "cues": [
            {"text": "left", "position": "bottom-left", "size": 92},
            {"text": "right", "position": "top-right", "color": "red"},
        ],
    }
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    styles = [ln for ln in ass_text(calls["vf"]).splitlines() if ln.startswith("Style:")]
    assert "Cue0" in styles[0] and ",92," in styles[0] and ",1,40,40," in styles[0]
    assert "Cue1" in styles[1] and ",40," in styles[1] and ",9,40,40," in styles[1]
    assert "&H000000FF" in styles[1]  # red, on that cue only


def test_overlay_cue_without_window_uses_the_block_one(tmp_path, calls):
    params = {"show": {"from": 2, "to": 8}, "cues": [{"text": "a"}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert "0:00:02.00,0:00:08.00" in dialogues(calls["vf"])[0]


def test_overlay_cue_text_takes_runs(tmp_path, calls):
    params = {"cues": [{"text": [{"text": "hi", "color": "yellow"}]}]}
    OverlayBlock().execute(params, ctx(tmp_path), "ov")
    assert r"{\c&H0000FFFF&}hi{\r}" in dialogues(calls["vf"])[0]


def test_overlay_cue_without_text_raises(tmp_path, calls):
    with pytest.raises(ValueError, match=r"overlay.cues\[1\]"):
        OverlayBlock().execute({"cues": [{"text": "a"}, {"size": 40}]}, ctx(tmp_path), "ov")


# --- image per clip ------------------------------------------------------------


def test_overlay_image_list_picks_one_per_clip(tmp_path, calls, png):
    second = tmp_path / "b.png"
    second.write_bytes(b"\x89PNG")
    params = {"image": [png, str(second)]}
    OverlayBlock().execute_item(params, {"clip": "c.mp4", "index": 1}, ctx(tmp_path), "ov")
    assert str(second) in calls["args"]


def test_overlay_image_list_shorter_than_channel_passes_through(tmp_path, calls, png):
    """Nothing left to draw on this clip — no overlay, and no needless re-encode."""
    res = OverlayBlock().execute_item(
        {"image": [png]}, {"clip": "c.mp4", "index": 3}, ctx(tmp_path), "ov"
    )
    assert res.item["clip"] == "c.mp4"
    assert "args" not in calls


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


def test_validator_accepts_placement_and_cues():
    doc = pipeline(
        {
            "position": "bottom-left",
            "margin_x": 62,
            "cues": [
                {"text": [{"text": "1.", "size": 92}], "show": {"from": 0, "to": 6}},
                {"text": "2.", "position": "top-right"},
            ],
        }
    )
    assert validate_doc(doc) == []


def test_validator_accepts_image_list():
    assert validate_doc(pipeline({"image": ["./a.png", "./b.png"]})) == []


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({}, "needs a 'text' (or 'cues') and/or an 'image'"),
        ({"image": 12}, "overlay.image must be an image path"),
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
        ({"text": "t", "position": "sideways"}, "unknown overlay.position"),
        ({"text": "t", "margin_x": "62px"}, "overlay.margin_x must be an integer"),
        ({"image": [12]}, "overlay.image must be an image path"),
        ({"cues": []}, "overlay.cues must be a non-empty list"),
        ({"cues": [{"show": {"from": 0}}]}, "overlay.cues[0].text"),
        ({"cues": [{"text": "a", "position": "sideways"}]}, "unknown overlay.cues[0].position"),
        ({"cues": [{"text": "a", "show": {"from": "5s", "to": "2s"}}]}, "overlay.cues[0].show.to"),
    ],
)
def test_validator_rejects_bad_overlay(params, needle):
    errors = validate_doc(pipeline(params))
    assert any(needle in e for e in errors), errors
