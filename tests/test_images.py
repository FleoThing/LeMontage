"""Tests for the image-folder input: validation, the `stills` producer, the
`still` image->clip block. FFmpeg is stubbed."""

import copy

import pytest

from lemontage.engine.blocks.still import StillBlock
from lemontage.engine.blocks.stills import StillsBlock, _list_images, _natural_key
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path, **kw):
    base = dict(vars={}, input={}, matrix={}, output_dir=tmp_path, pipeline_name="demo")
    base.update(kw)
    return RunContext(**base)


def make_images(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"\x89PNG\r\n")  # content irrelevant; listing is by extension
    return folder


# --- validator: images input -----------------------------------------------

_IMAGES_DOC = {
    "lemontage": "1.0",
    "name": "slideshow",
    "input": {"type": "images", "source": "./photos/"},
    "steps": [{"stills": {"emit": "shots"}}, {"still": {"from": "shots"}}],
}


def test_images_input_accepted():
    assert validate_doc(copy.deepcopy(_IMAGES_DOC)) == []


def test_images_source_rejects_mp4():
    d = copy.deepcopy(_IMAGES_DOC)
    d["input"]["source"] = "./clip.mp4"
    errors = validate_doc(d)
    assert any("must be a folder of images" in e for e in errors)


def test_unknown_input_type_lists_images():
    d = copy.deepcopy(_IMAGES_DOC)
    d["input"]["type"] = "gif"
    errors = validate_doc(d)
    assert any("images" in e and "unknown input.type" in e for e in errors)


# --- stills producer --------------------------------------------------------


def test_natural_sort_orders_numbers():
    from pathlib import Path

    names = [Path(n) for n in ["img10.png", "img2.png", "img1.png"]]
    assert sorted(names, key=_natural_key) == [
        Path("img1.png"),
        Path("img2.png"),
        Path("img10.png"),
    ]


def test_list_images_filters_and_sorts(tmp_path):
    folder = make_images(tmp_path / "p", ["b.png", "a.jpg", "note.txt", "c.webp"])
    names = [p.name for p in _list_images(str(folder))]
    assert names == ["a.jpg", "b.png", "c.webp"]  # .txt dropped, sorted


def test_stills_emits_one_item_per_image(tmp_path):
    folder = make_images(tmp_path / "p", ["s1.png", "s2.png", "s3.png"])
    result = StillsBlock().execute({"input": str(folder), "duration": "2s"}, ctx(tmp_path), "st")
    assert result.outputs["count"] == 3
    assert result.channel_items[0] == {"index": 0, "image": str(folder / "s1.png"), "duration": 2.0}
    assert [it["index"] for it in result.channel_items] == [0, 1, 2]


def test_stills_max_caps_count(tmp_path):
    folder = make_images(tmp_path / "p", ["a.png", "b.png", "c.png", "d.png"])
    result = StillsBlock().execute({"input": str(folder), "max": 2}, ctx(tmp_path), "st")
    assert result.outputs["count"] == 2


def test_stills_shuffle_is_deterministic(tmp_path):
    folder = make_images(tmp_path / "p", [f"{i}.png" for i in range(6)])
    a = StillsBlock().execute(
        {"input": str(folder), "shuffle": True, "seed": 7}, ctx(tmp_path), "s"
    )
    b = StillsBlock().execute(
        {"input": str(folder), "shuffle": True, "seed": 7}, ctx(tmp_path), "s"
    )
    order = [it["image"] for it in a.channel_items]
    assert order == [it["image"] for it in b.channel_items]  # same seed -> same order


def test_stills_empty_folder_raises(tmp_path):
    folder = make_images(tmp_path / "p", ["readme.txt"])
    with pytest.raises(ValueError, match="no images found"):
        StillsBlock().execute({"input": str(folder)}, ctx(tmp_path), "st")


def test_stills_requires_a_source(tmp_path):
    with pytest.raises(ValueError, match="no image folder"):
        StillsBlock().execute({}, ctx(tmp_path), "st")


# --- still: image -> clip ---------------------------------------------------


def test_still_renders_image_to_clip(tmp_path, monkeypatch):
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.5}
    result = StillBlock().execute_item({"fps": 24}, item, ctx(tmp_path), "sc")

    args = captured["args"]
    assert "-loop" in args and "1" in args
    assert str(tmp_path / "a.png") in args
    assert "-t" in args and "2.500" in args  # per-item duration
    assert result.item["clip"].endswith("sc-0.mp4")


def test_still_item_requires_image(tmp_path):
    with pytest.raises(ValueError, match="no 'image'"):
        StillBlock().execute_item({}, {"index": 0}, ctx(tmp_path), "sc")


def test_still_zoomout_builds_zoompan(tmp_path, monkeypatch):
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(still_mod.ffmpeg, "probe_resolution", lambda _f: (1081, 1920))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item({"motion": "zoomout", "fps": 30}, item, ctx(tmp_path), "sc")

    graph = captured["args"][captured["args"].index("-vf") + 1]
    # 2s * 30fps -> 60 frames; cubic ease-out, so it brakes into the landing.
    assert "zoompan=z='1+(1.1-1)*pow(1-min(on/59,1),3)'" in graph
    assert "s=1080x1920" in graph  # odd source width rounded down to even


def test_still_zoomin_decelerates_into_the_landing(tmp_path, monkeypatch):
    """The "smooth zoom": fast out of the gate, braking onto the target.

    Checked on the numbers rather than the string, because that is what a viewer
    sees: 1.0 -> `amount`, monotone (no overshoot, no wobble), and each step
    smaller than the last — a constant-speed ramp would keep them equal.
    """
    import math

    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(still_mod.ffmpeg, "probe_resolution", lambda _f: (1080, 1920))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item(
        {"motion": "zoomin", "fps": 30, "motion_amount": 1.4}, item, ctx(tmp_path), "sc"
    )

    graph = captured["args"][captured["args"].index("-vf") + 1]
    expr = graph.split("zoompan=z='")[1].split("':x=")[0]
    zoom = [eval(expr.replace("on", str(n)), {"pow": math.pow, "min": min}) for n in range(60)]
    assert zoom[0] == pytest.approx(1.0)
    assert zoom[-1] == pytest.approx(1.4)
    assert max(zoom) == pytest.approx(1.4)  # lands on it, never past it
    steps = [b - a for a, b in zip(zoom, zoom[1:], strict=False)]
    assert all(s >= 0 for s in steps)  # monotone push-in
    assert all(b <= a + 1e-9 for a, b in zip(steps, steps[1:], strict=False))  # each step slower
    assert steps[0] > steps[-2] * 5  # and decisively so, not a near-linear ramp


def test_still_zoom_aims_at_the_focal_point(tmp_path, monkeypatch):
    """A punch-in into the middle of a portrait lands on the torso, not the face —
    the window travels straight to `focal_point` instead, indexed on the zoom so it
    arrives with it (a centre pinned on the focal point at every zoom detours)."""
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(still_mod.ffmpeg, "probe_resolution", lambda _f: (1080, 1920))
    monkeypatch.setattr(still_mod.smartcrop, "focal_point", lambda _i: (0.8, 0.2))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item({"motion": "zoomin", "fps": 30}, item, ctx(tmp_path), "sc")

    graph = captured["args"][captured["args"].index("-vf") + 1]
    aim = "min(1,(zoom-1)/(1.1-1))"  # clamped: the bounce must not drag the aim past the face
    assert f"x='max(0,min(iw-iw/zoom,(0.5+(0.8000-0.5)*{aim})*iw-(iw/zoom/2)))'" in graph
    assert f"y='max(0,min(ih-ih/zoom,(0.5+(0.2000-0.5)*{aim})*ih-(ih/zoom/2)))'" in graph


def test_still_zoom_without_a_focal_point_stays_centred(tmp_path, monkeypatch):
    """No OpenCV / unreadable image -> the old centred move, not a crash."""
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(still_mod.ffmpeg, "probe_resolution", lambda _f: (1080, 1920))
    monkeypatch.setattr(still_mod.smartcrop, "focal_point", lambda _i: None)
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item({"motion": "zoomout", "fps": 30}, item, ctx(tmp_path), "sc")

    graph = captured["args"][captured["args"].index("-vf") + 1]
    # == iw/2-(iw/zoom/2): the centred move, unchanged.
    assert "(0.5+(0.5000-0.5)*min(1,(zoom-1)/(1.1-1)))*iw-(iw/zoom/2)" in graph


def test_still_panup_is_a_pure_scroll(tmp_path, monkeypatch):
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item(
        {"motion": "panup", "motion_amount": 1.3, "fps": 30}, item, ctx(tmp_path), "sc"
    )

    graph = captured["args"][captured["args"].index("-vf") + 1]
    assert "zoompan" not in graph  # a moving crop, not a zoom
    assert "crop=w=iw:h=ih/1.3:x=0:y='(min(t/2.000,1))*(ih-oh)'" in graph


def test_still_pandown_reverses_the_scroll(tmp_path, monkeypatch):
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item(
        {"motion": "pandown", "motion_duration": "1s", "fps": 30}, item, ctx(tmp_path), "sc"
    )

    graph = captured["args"][captured["args"].index("-vf") + 1]
    assert ":y='(1-min(t/1.000,1))*(ih-oh)'" in graph  # starts at the bottom, 1s scroll


def test_still_zoomout_motion_duration_shortens_span(tmp_path, monkeypatch):
    from lemontage.engine.blocks import still as still_mod

    captured = {}
    monkeypatch.setattr(still_mod.ffmpeg, "run", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(still_mod.ffmpeg, "probe_resolution", lambda _f: (1080, 1920))
    item = {"index": 0, "image": str(tmp_path / "a.png"), "duration": 2.0}
    StillBlock().execute_item(
        {"motion": "zoomout", "motion_duration": "0.3s", "fps": 30}, item, ctx(tmp_path), "sc"
    )

    graph = captured["args"][captured["args"].index("-vf") + 1]
    assert "pow(1-min(on/9,1),3)" in graph  # 0.3s * 30fps -> 9-frame pull-back
    assert "d=60" in graph  # ...within a 60-frame clip (holds full frame after)


def test_still_unknown_motion_raises(tmp_path):
    item = {"index": 0, "image": str(tmp_path / "a.png")}
    with pytest.raises(ValueError, match="unknown motion"):
        StillBlock().execute_item({"motion": "spin"}, item, ctx(tmp_path), "sc")


def test_still_bad_motion_amount_raises(tmp_path):
    item = {"index": 0, "image": str(tmp_path / "a.png")}
    with pytest.raises(ValueError, match="motion_amount"):
        StillBlock().execute_item(
            {"motion": "zoomout", "motion_amount": 0.9}, item, ctx(tmp_path), "sc"
        )
