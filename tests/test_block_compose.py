"""Tests for the `compose` block: canvas, layer geometry, keying and audio.

The rendering itself is FFmpeg's job; what is worth pinning down here is
everything decided *before* the render: the geometry a layer resolves to, the
filter chain a `key:` produces, and the mistakes that must be refused rather
than composited into a silently wrong frame.
"""

from pathlib import Path

import pytest

from lemontage.engine.blocks.compose import (
    _audio_args,
    _canvas,
    _channel_file,
    _extent,
    _fit_chain,
    _key_filter,
    _rect,
    _timing,
)
from lemontage.engine.context import RunContext


def layer(**params):
    return {"params": params, "index": 0}


# --- the canvas ------------------------------------------------------------


def test_format_preset_gives_its_resolution():
    assert _canvas({"format": "vertical"}) == (1080, 1920)
    assert _canvas({"format": "square"}) == (1080, 1080)


def test_canvas_defaults_to_vertical():
    assert _canvas({}) == (1080, 1920)


def test_explicit_size_overrides_the_preset():
    assert _canvas({"size": "1440x1440", "format": "vertical"}) == (1440, 1440)


@pytest.mark.parametrize("bad", ["1080", "1080*1920", "wide x tall", "1x1"])
def test_unusable_size_is_refused(bad):
    with pytest.raises(ValueError, match="size"):
        _canvas({"size": bad})


def test_unknown_format_lists_the_valid_ones():
    with pytest.raises(ValueError, match="unknown format"):
        _canvas({"format": "cinema"})


# --- geometry --------------------------------------------------------------


def test_int_extent_is_pixels_and_percent_is_a_share():
    assert _extent(540, 1080, "w") == 540
    assert _extent("50%", 1080, "w") == 540
    assert _extent("33.5%", 1000, "w") == 335


@pytest.mark.parametrize("bad", ["half", "50 %%", True, "%"])
def test_unreadable_extent_is_refused(bad):
    with pytest.raises(ValueError):
        _extent(bad, 1080, "layer 0 width")


def test_a_layer_fills_the_canvas_by_default():
    assert _rect(layer(), (1080, 1920)) == (0, 0, 1080, 1920)


def test_bottom_half_is_expressed_in_percent():
    assert _rect(layer(y="50%", height="50%"), (1080, 1920)) == (0, 960, 1080, 960)


def test_the_same_layout_replays_at_another_canvas_size():
    """The point of percentages: one composition, several formats."""
    spec = dict(y="50%", height="50%")
    assert _rect(layer(**spec), (1080, 1080)) == (0, 540, 1080, 540)
    assert _rect(layer(**spec), (1920, 1080)) == (0, 540, 1920, 540)


def test_negative_coordinates_count_back_from_the_far_edge():
    # x: -40 seats a 200px layer 40px in from the right of a 1080 canvas.
    assert _rect(layer(x=-40, y=40, width=200, height=200), (1080, 1920)) == (840, 40, 200, 200)


def test_a_layer_too_small_to_render_is_refused():
    with pytest.raises(ValueError, match="too small"):
        _rect(layer(width="0%"), (1080, 1920))


# --- fit -------------------------------------------------------------------


def test_cover_crops_to_fill_the_rect():
    chain, offset = _fit_chain(layer(fit="cover"), 1080, 960)
    assert "force_original_aspect_ratio=increase" in chain[0]
    assert "crop=1080:960" in chain[1]
    assert offset == "0:0"


def test_contain_centres_without_padding():
    """Padding would hide the layers underneath, which is what compositing is for."""
    chain, offset = _fit_chain(layer(fit="contain"), 1080, 960)
    assert "force_original_aspect_ratio=decrease" in chain[0]
    assert not any("pad=" in step for step in chain)
    assert offset == "(1080-w)/2:(960-h)/2"


def test_unknown_fit_is_refused():
    with pytest.raises(ValueError, match="unknown fit"):
        _fit_chain(layer(fit="squish"), 100, 100)


# --- keying ----------------------------------------------------------------


def test_no_key_means_no_filter():
    assert _key_filter({}, 0) == []


def test_keying_writes_alpha_before_it_can_be_used():
    """chromakey writes an alpha plane the source pixel format does not have."""
    chain = _key_filter({"key": {"color": "green"}}, 0)
    assert chain[0] == "format=yuva420p"
    assert chain[1].startswith("chromakey=0x00FF00:")


def test_the_default_tolerance_stays_narrow():
    """A wide tolerance keys the subject too: at 0.3 the person goes translucent."""
    chain = _key_filter({"key": True}, 0)
    assert "chromakey=0x00FF00:0.12:0.02" in chain


def test_keying_despills_by_default():
    """Green bounced off the screen tints hair edges; keying alone leaves a halo."""
    assert any("despill=type=green" in step for step in _key_filter({"key": True}, 0))


def test_despill_can_be_turned_off():
    chain = _key_filter({"key": {"spill": 0}}, 0)
    assert not any("despill" in step for step in chain)


def test_a_blue_screen_despills_blue():
    chain = _key_filter({"key": {"color": "blue"}}, 0)
    assert any("despill=type=blue" in step for step in chain)


def test_a_custom_hex_key_is_accepted():
    chain = _key_filter({"key": {"color": "#00b140"}}, 0)
    assert "chromakey=0x00b140:0.12:0.02" in chain


@pytest.mark.parametrize("bad", ["puce", "#fff", "00ff00"])
def test_an_unusable_key_colour_is_refused(bad):
    with pytest.raises(ValueError, match="key.color"):
        _key_filter({"key": {"color": bad}}, 0)


@pytest.mark.parametrize("field", ["tolerance", "softness", "spill"])
def test_key_knobs_outside_0_1_are_refused_not_clamped(field):
    with pytest.raises(ValueError, match=field):
        _key_filter({"key": {field: 4}}, 0)


# --- duration --------------------------------------------------------------


def test_a_still_is_looped_to_the_full_duration():
    filters, flags = _timing({"params": {}, "index": 0, "is_video": False}, 9.0)
    assert filters == []
    assert flags == ["-loop", "1", "-t", "9.000"]


def test_a_short_layer_holds_its_last_frame_by_default():
    filters, flags = _timing({"params": {}, "index": 0, "is_video": True}, 9.0)
    assert filters == ["tpad=stop_mode=clone:stop_duration=9.000"]
    assert flags == []


def test_a_looping_layer_restarts_at_the_input():
    filters, flags = _timing({"params": {"on_short": "loop"}, "index": 0, "is_video": True}, 9.0)
    assert filters == []
    assert flags == ["-stream_loop", "-1"]


def test_a_hidden_layer_just_ends():
    filters, flags = _timing({"params": {"on_short": "hide"}, "index": 0, "is_video": True}, 9.0)
    assert (filters, flags) == ([], [])


def test_unknown_on_short_is_refused():
    with pytest.raises(ValueError, match="unknown on_short"):
        _timing({"params": {"on_short": "stretch"}, "index": 0, "is_video": True}, 9.0)


# --- audio -----------------------------------------------------------------


def video(index, audio=True):
    return {"index": index, "is_video": True, "source": f"v{index}", "_audio": audio}


def test_audio_none_mutes(monkeypatch):
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: True)
    assert _audio_args({"audio": "none"}, [video(0)]) == ["-an"]


def test_audio_defaults_to_the_first_audible_layer(monkeypatch):
    # Layer 0 is a still, layer 1 the video that actually carries sound.
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: True)
    layers = [{"index": 0, "is_video": False, "source": "bg.jpg"}, video(1)]
    assert _audio_args({}, layers) == ["-map", "1:a"]


def test_a_composition_with_no_sound_anywhere_is_muted(monkeypatch):
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: False)
    assert _audio_args({}, [video(0)]) == ["-an"]


def test_mix_folds_every_audible_layer(monkeypatch):
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: True)
    args = _audio_args({"audio": "mix"}, [video(0), video(1)])
    assert "[0:a][1:a]amix=inputs=2:normalize=0[mixed]" in args


def test_choosing_a_silent_image_layer_is_refused(monkeypatch):
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: True)
    layers = [{"index": 0, "is_video": False, "source": "bg.jpg"}, video(1)]
    with pytest.raises(ValueError, match="has no sound"):
        _audio_args({"audio": 0}, layers)


def test_an_out_of_range_audio_layer_is_refused(monkeypatch):
    monkeypatch.setattr("lemontage.engine.ffmpeg.has_audio", lambda _: True)
    with pytest.raises(ValueError, match="out of range"):
        _audio_args({"audio": 7}, [video(0)])


# --- channels --------------------------------------------------------------


def context(channels):
    return RunContext(
        vars={}, input={}, matrix={}, output_dir=Path("/out"), pipeline_name="p", channels=channels
    )


def test_a_layer_source_that_names_no_channel_is_a_path():
    assert _channel_file("./clip.mp4", context({})) == "./clip.mp4"


def test_a_single_clip_channel_resolves_to_its_file():
    ctx = context({"reel": [{"index": 0, "file": "/out/reel.mp4"}]})
    assert _channel_file("reel", ctx) == "/out/reel.mp4"


def test_a_multi_clip_channel_is_refused_rather_than_silently_truncated():
    ctx = context({"clips": [{"index": 0, "file": "a.mp4"}, {"index": 1, "file": "b.mp4"}]})
    with pytest.raises(ValueError, match="run 'concat' on it first"):
        _channel_file("clips", ctx)
