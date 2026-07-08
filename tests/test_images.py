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
    assert "zoompan=z='1+(1.1-1)*pow(1-min(on/59,1),2)'" in graph  # 2s * 30fps -> 60 frames
    assert "s=1080x1920" in graph  # odd source width rounded down to even


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
    assert "pow(1-min(on/9,1),2)" in graph  # 0.3s * 30fps -> 9-frame pull-back
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
