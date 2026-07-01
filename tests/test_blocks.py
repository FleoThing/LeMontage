"""Tests for the native blocks.

Heavy backends (FFmpeg, Whisper) are mocked; these tests pin the block logic —
output shapes, path naming, caption generation, clip windowing.
"""

from pathlib import Path

import pytest

from lemontage.engine import ffmpeg, providers
from lemontage.engine.blocks.captions import (
    CaptionsBlock,
    _ass_time,
    _build_lines,
    _dialogue,
    _lines_from_words,
)
from lemontage.engine.blocks.detect_clips import (
    _random_clips,
    _select_loud_clips,
    _windowed_clips,
)
from lemontage.engine.blocks.export import (
    ExportBlock,
    _muted,
    _output_path,
    _scale_chain,
    _target_size,
    _title_align,
    _title_ass,
    _title_border,
    _title_window,
)
from lemontage.engine.blocks.stt import SttBlock
from lemontage.engine.context import RunContext
from lemontage.engine.providers.base import Segment, Transcript, Word


def ctx(tmp_path, **kw):
    base = dict(
        vars={},
        input={"source": "ep.mp4"},
        matrix={},
        output_dir=tmp_path,
        pipeline_name="demo",
    )
    base.update(kw)
    return RunContext(**base)


# --- stt -------------------------------------------------------------------


def test_stt_maps_transcript_to_outputs(tmp_path, monkeypatch):
    class FakeSTT:
        def transcribe(self, media, lang="auto", **options):
            return Transcript(
                text="hello world",
                segments=[
                    Segment(0.0, 1.0, "hello", words=[Word(0.0, 0.5, "hello")]),
                    Segment(1.0, 2.0, "world", words=[Word(1.0, 1.5, "world")]),
                ],
                lang="en",
            )

    monkeypatch.setattr(providers, "default_stt", lambda model="base": FakeSTT())
    out = SttBlock().execute({"model": "base", "lang": "en"}, ctx(tmp_path), "t").outputs
    assert out["text"] == "hello world"
    assert out["lang"] == "en"
    assert out["segments"][0]["text"] == "hello"
    # flat word list exposed for karaoke captions
    assert out["words"] == [
        {"start": 0.0, "end": 0.5, "text": "hello"},
        {"start": 1.0, "end": 1.5, "text": "world"},
    ]


def test_stt_requires_media(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "default_stt", lambda model="base": None)
    with pytest.raises(ValueError):
        SttBlock().execute({}, ctx(tmp_path, input={}), "t")


# --- detect_clips windowing ------------------------------------------------


def test_windowed_clips_splits_long_spans():
    clips = _windowed_clips([(0.0, 130.0)], min_dur=15, max_dur=60, max_clips=5)
    assert clips == [(0.0, 60.0), (60.0, 120.0)]  # 120-130 leftover < min


def test_windowed_clips_respects_max_clips():
    clips = _windowed_clips([(0.0, 1000.0)], min_dur=10, max_dur=30, max_clips=3)
    assert len(clips) == 3


def test_windowed_clips_drops_short_spans():
    clips = _windowed_clips([(0.0, 5.0), (10.0, 40.0)], min_dur=15, max_dur=60, max_clips=5)
    assert clips == [(10.0, 40.0)]


# --- detect_clips loudness auto-framing ------------------------------------


def _timeline(loud_ranges, total=200, baseline=-30.0, loud=-8.0):
    """Build a 1s-spaced timeline that is quiet except in the given ranges."""
    levels = []
    for t in range(total):
        level = loud if any(a <= t < b for a, b in loud_ranges) else baseline
        levels.append((float(t), level))
    return levels


def test_select_loud_clips_anchors_before_onset_and_fills_forward():
    # Loud from 40s..50s: clip starts ~3s before the onset and runs for max_dur,
    # so the (visually interesting) aftermath is kept.
    ((start, end),) = _select_loud_clips(
        _timeline([(40, 50)]), total=200.0, min_dur=8, max_dur=30, max_clips=1
    )
    assert round(start) == 37  # onset 40 - 3s lead-in
    assert round(end - start) == 30  # fills the requested length forward
    assert start <= 40 and end >= 50  # the loud moment is inside


def test_select_loud_clips_pads_to_min_duration_near_end():
    # Near the end of the media the forward window is short -> padded to min_dur.
    ((start, end),) = _select_loud_clips(
        _timeline([(195, 196)]), total=200.0, min_dur=15, max_dur=40, max_clips=1
    )
    assert round(end - start) == 15
    assert end <= 200


def test_select_loud_clips_separate_regions_and_cap():
    clips = _select_loud_clips(
        _timeline([(30, 35), (90, 95), (150, 155)]), total=200.0, min_dur=8, max_dur=20, max_clips=2
    )
    assert len(clips) == 2  # two loudest distinct regions, capped
    assert clips == sorted(clips)  # chronological


def test_select_loud_clips_only_loud_regions_qualify():
    # max_clips=5 but only one loud region -> only one clip (no quiet filler).
    clips = _select_loud_clips(
        _timeline([(40, 50)]), total=200.0, min_dur=8, max_dur=20, max_clips=5
    )
    assert len(clips) == 1


def test_select_loud_clips_empty_timeline():
    assert _select_loud_clips([], total=100.0, min_dur=8, max_dur=20, max_clips=5) == []


# --- detect_clips random ----------------------------------------------------


def test_random_clips_count_lengths_and_no_overlap():
    import random

    clips = _random_clips(60.0, 1.3, 1.3, 8, random.Random(42))
    assert len(clips) == 8
    for i, (start, end) in enumerate(clips):
        assert 0 <= start < end <= 60.0
        assert abs((end - start) - 1.3) < 1e-6  # fixed length min==max
        if i:
            assert start >= clips[i - 1][1]  # non-overlapping, chronological


def test_random_clips_advance_through_the_video():
    import random

    # Each clip must start after the previous one ends (forward walk), so a
    # passage is never picked twice.
    clips = _random_clips(120.0, 2.0, 4.0, 6, random.Random(3))
    starts = [s for s, _ in clips]
    assert starts == sorted(starts)
    for i in range(1, len(clips)):
        assert clips[i][0] >= clips[i - 1][1]


def test_random_clips_is_reproducible_with_seed():
    import random

    a = _random_clips(60.0, 2.0, 5.0, 5, random.Random(7))
    b = _random_clips(60.0, 2.0, 5.0, 5, random.Random(7))
    assert a == b


def test_random_clips_capped_by_available_space():
    import random

    # total 3s, min 1.3s -> at most 2 fit even though max_clips=8
    assert len(_random_clips(3.0, 1.3, 1.3, 8, random.Random(0))) == 2


def test_random_clips_empty_when_too_short():
    import random

    assert _random_clips(1.0, 1.3, 1.3, 8, random.Random(0)) == []


# --- captions (word-level karaoke) -----------------------------------------


def _words(*specs):
    return [{"start": s, "end": e, "text": t} for s, e, t in specs]


def test_lines_from_words_groups_by_char_limit():
    words = _words((0.0, 0.3, "one"), (0.3, 0.6, "two"), (0.6, 0.9, "three"), (0.9, 1.2, "four"))
    lines = _lines_from_words(words, offset=0.0, max_chars=9)
    assert len(lines) >= 2
    assert all(len(line["text"]) <= 11 for line in lines)


def test_lines_from_words_breaks_on_big_gap():
    words = _words((0.0, 0.3, "hi"), (5.0, 5.3, "later"))  # >1.2s gap -> new line
    lines = _lines_from_words(words, offset=0.0, max_chars=99)
    assert len(lines) == 2


def test_lines_from_words_applies_offset_and_drops_past_words():
    words = _words((1.0, 1.5, "before"), (11.0, 11.5, "after"))
    lines = _lines_from_words(words, offset=10.0, max_chars=99)
    assert len(lines) == 1
    assert lines[0]["text"] == "after"
    assert lines[0]["start"] == 1.0  # 11.0 - 10.0


def test_dialogue_emits_karaoke_tags_absorbing_gaps():
    line = {
        "start": 0.0,
        "end": 1.0,
        "text": "a b",
        "words": [{"start": 0.0, "end": 0.3, "text": "a"}, {"start": 0.5, "end": 1.0, "text": "b"}],
    }
    d = _dialogue(line)
    # first word's \k spans to the next word's start (0.5s -> 50cs), absorbing the gap
    assert r"{\k50}a" in d
    assert r"{\k50}b" in d  # last word: its own 0.5s duration


def test_dialogue_plain_text_for_segment_fallback():
    line = {"start": 0.0, "end": 1.0, "text": "hello world", "words": []}
    assert _dialogue(line).endswith(",hello world")


def test_ass_time_format():
    assert _ass_time(75.5) == "0:01:15.50"


def test_build_lines_prefers_words_over_segments():
    params = {"words": _words((0.0, 0.5, "hi")), "segments": [{"start": 0, "end": 9, "text": "x"}]}
    lines = _build_lines(params, offset=0.0)
    assert lines[0]["words"]  # used word timing, not the segment


def test_stt_forwards_vad_and_beam_options(tmp_path, monkeypatch):
    captured = {}

    class FakeSTT:
        def transcribe(self, media, lang="auto", **options):
            captured.update(options)
            return Transcript(text="", segments=[], lang="en")

    monkeypatch.setattr(providers, "default_stt", lambda model="base": FakeSTT())
    SttBlock().execute({"vad_filter": True, "beam_size": 3}, ctx(tmp_path), "t")
    assert captured == {"vad_filter": True, "beam_size": 3}


def test_captions_requires_words_or_segments(tmp_path):
    with pytest.raises(ValueError):
        CaptionsBlock().execute({}, ctx(tmp_path), "c")


# --- export ----------------------------------------------------------------


def test_target_size_from_format_and_resolution():
    assert _target_size({"format": "vertical"}) == (1080, 1920)
    assert _target_size({"format": "square"}) == (1080, 1080)
    assert _target_size({"resolution": "540x960"}) == (540, 960)


def test_output_path_template_substitution(tmp_path):
    c = ctx(tmp_path)
    template = str(tmp_path / "out" / "{{ name }}-{{ index }}.mp4")
    p = _output_path({"output": template}, c, index=2)
    assert p.name == "demo-2.mp4"
    assert p.parent == tmp_path / "out"


def test_output_path_supports_part_token(tmp_path):
    # {{ part }} is 1-based (index + 1), like the title — and must be substituted
    # in the path, else mapped clips collide on one literal "{{ part }}" file.
    c = ctx(tmp_path)
    template = str(tmp_path / "out" / "{{ name }}-{{ part }}.mp4")
    assert _output_path({"output": template}, c, index=0).name == "demo-1.mp4"
    assert _output_path({"output": template}, c, index=2).name == "demo-3.mp4"


def test_output_path_distinct_per_clip_for_part_template(tmp_path):
    # Regression: each mapped clip must resolve to a UNIQUE path (no collision).
    c = ctx(tmp_path)
    template = str(tmp_path / "{{ name }}-{{ part }}.mp4")
    paths = {_output_path({"output": template}, c, index=i) for i in range(5)}
    assert len(paths) == 5


def test_output_path_default(tmp_path):
    p = _output_path({}, ctx(tmp_path), index=1)
    assert p == tmp_path / "demo-1.mp4"


def test_title_ass_none_without_title(tmp_path):
    assert _title_ass({}, ctx(tmp_path), "t") is None


def test_title_ass_writes_playres_and_lines(tmp_path):
    params = {
        "format": "vertical",
        "title": "UFC Maison Blanche\nCyril Gane vs Pereira",
        "title_size": 40,
    }
    path = _title_ass(params, ctx(tmp_path), "export-0-title")
    content = path.read_text()
    assert "PlayResX: 1080" in content and "PlayResY: 1920" in content
    assert "Anton,40," in content  # default font + size honoured
    # Two lines joined with the ASS line break.
    assert r"UFC Maison Blanche\NCyril Gane vs Pereira" in content


def test_title_ass_accepts_literal_backslash_n(tmp_path):
    path = _title_ass({"title": "line one\\nline two"}, ctx(tmp_path), "t")
    assert r"line one\Nline two" in path.read_text()


def test_title_window_defaults_to_whole_clip():
    assert _title_window({}) == ("0:00:00.00", "9:59:59.99")


def test_title_window_duration_sets_end():
    assert _title_window({"title_duration": "2s"}) == ("0:00:00.00", "0:00:02.00")


def test_title_window_start_and_end():
    assert _title_window({"title_start": "1s", "title_end": "3s"}) == ("0:00:01.00", "0:00:03.00")


def test_title_window_rejects_end_before_start():
    with pytest.raises(ValueError, match="end must be after"):
        _title_window({"title_start": "3s", "title_end": "1s"})


def test_title_fade_injects_ass_fad_tag(tmp_path):
    content = _title_ass({"title": "hi", "title_fade": "0.3s"}, ctx(tmp_path), "t").read_text()
    dialogue = next(ln for ln in content.splitlines() if ln.startswith("Dialogue"))
    assert r"{\fad(300,300)}" in dialogue


def test_title_without_fade_has_no_fad_tag(tmp_path):
    content = _title_ass({"title": "hi"}, ctx(tmp_path), "t").read_text()
    assert r"\fad" not in content


def test_title_fade_list_applies_per_clip(tmp_path):
    params = {"title": "hi", "title_fade": [0, 0, "0.4s"]}
    # clip 0: no fade
    assert r"\fad" not in _title_ass(params, ctx(tmp_path), "t0", index=0).read_text()
    # clip 2: 0.4s fade
    assert r"{\fad(400,400)}" in _title_ass(params, ctx(tmp_path), "t2", index=2).read_text()


def test_title_color_sets_ass_primary(tmp_path):
    content = _title_ass({"title": "hi", "title_color": "#FFCC00"}, ctx(tmp_path), "t").read_text()
    style = next(ln for ln in content.splitlines() if ln.startswith("Style: Title"))
    assert "&H0000CCFF" in style  # #FFCC00 -> ASS &H00BBGGRR


def test_title_color_list_applies_per_clip(tmp_path):
    params = {"title": "hi", "title_color": ["red", None, "blue"]}

    def primary(index):
        content = _title_ass(params, ctx(tmp_path), f"t{index}", index=index).read_text()
        style = next(ln for ln in content.splitlines() if ln.startswith("Style: Title"))
        return style.split(",")[3]

    assert primary(0) == "&H000000FF"  # red
    assert primary(2) == "&H00FF0000"  # blue


def test_title_color_defaults_to_white(tmp_path):
    content = _title_ass({"title": "hi"}, ctx(tmp_path), "t").read_text()
    style = next(ln for ln in content.splitlines() if ln.startswith("Style: Title"))
    assert "&H00FFFFFF" in style


def test_title_clips_restricts_to_listed_clips(tmp_path):
    params = {"title": "hi", "title_clips": [0]}
    assert _title_ass(params, ctx(tmp_path), "t0", index=0) is not None  # first clip: title
    assert _title_ass(params, ctx(tmp_path), "t1", index=1) is None  # others: no title


def test_title_ass_writes_the_window_into_the_dialogue(tmp_path):
    content = _title_ass({"title": "hi", "title_duration": "2s"}, ctx(tmp_path), "t").read_text()
    dialogue = next(ln for ln in content.splitlines() if ln.startswith("Dialogue"))
    assert dialogue.split(",")[1:3] == ["0:00:00.00", "0:00:02.00"]


def test_title_ass_default_font(tmp_path):
    content = _title_ass({"title": "Hi"}, ctx(tmp_path), "t").read_text()
    assert "Style: Title,Anton," in content  # default preset font1


def test_title_ass_font_alias_resolves(tmp_path):
    content = _title_ass({"title": "Hi", "title_font": "font2"}, ctx(tmp_path), "t").read_text()
    assert "Style: Title,Bebas Neue," in content


def test_title_ass_literal_font_passes_through(tmp_path):
    content = _title_ass({"title": "Hi", "title_font": "Impact"}, ctx(tmp_path), "t").read_text()
    assert "Style: Title,Impact," in content


def test_title_part_token_is_one_based(tmp_path):
    content = _title_ass({"title": "Moment #{{ part }}"}, ctx(tmp_path), "t", index=2).read_text()
    assert "Moment #3" in content  # index 2 -> part 3


def test_title_name_and_index_tokens(tmp_path):
    content = _title_ass(
        {"title": "{{ name }} {{ index }}"}, ctx(tmp_path, pipeline_name="demo"), "t", index=1
    ).read_text()
    assert "demo 1" in content


# --- concat -----------------------------------------------------------------


def test_concat_joins_channel_files_in_order(tmp_path, monkeypatch):
    from lemontage.engine import ffmpeg as ff
    from lemontage.engine.blocks.concat import ConcatBlock

    calls = {}

    def fake_run(args):
        calls["args"] = args
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ff, "run", fake_run)
    items = [  # given out of order -> concat must sort by index
        {"index": 1, "file": str(tmp_path / "b.mp4")},
        {"index": 0, "file": str(tmp_path / "a.mp4")},
    ]
    out = ConcatBlock().execute_channel(
        {"output": str(tmp_path / "reel.mp4")}, items, ctx(tmp_path), "concat"
    )
    assert out.outputs["file"] == str(tmp_path / "reel.mp4")
    list_file = next(tmp_path.glob("**/concat-list.txt"))
    lines = list_file.read_text().splitlines()
    assert lines[0].endswith("a.mp4'") and lines[1].endswith("b.mp4'")  # sorted by index


def test_concat_single_mode_requires_channel(tmp_path):
    from lemontage.engine.blocks.concat import ConcatBlock

    with pytest.raises(ValueError):
        ConcatBlock().execute({}, ctx(tmp_path), "concat")


def test_scale_chain_contain_letterboxes(tmp_path):
    chain = _scale_chain({}, 1080, 1920)  # default fit
    assert any("force_original_aspect_ratio=decrease" in f for f in chain)
    assert any(f.startswith("pad=") for f in chain)


def test_scale_chain_cover_crops_without_bars(tmp_path):
    chain = _scale_chain({"fit": "cover"}, 1080, 1920)
    assert any("force_original_aspect_ratio=increase" in f for f in chain)
    assert any(f.startswith("crop=1080:1920") for f in chain)
    assert not any(f.startswith("pad=") for f in chain)  # no black bars


def test_scale_chain_unknown_fit_raises():
    with pytest.raises(ValueError, match="unknown fit"):
        _scale_chain({"fit": "zoom"}, 1080, 1920)


def test_bg_color_sets_pad_colour():
    pad = _scale_chain({"fit": "contain", "bg": "white"}, 1080, 1920)[-1]
    assert pad.startswith("pad=") and "color=white" in pad
    hexpad = _scale_chain({"fit": "contain", "bg": "#101010"}, 1080, 1920)[-1]
    assert "color=0x101010" in hexpad  # # -> 0x for ffmpeg


def test_bg_blur_builds_split_overlay():
    chain = _scale_chain({"fit": "contain", "bg": "blur"}, 1080, 1920)
    graph = chain[-1]
    assert graph.startswith("split=2")
    assert "gblur=" in graph  # blurred background
    assert graph.rstrip().endswith("overlay=(W-w)/2:(H-h)/2")  # fitted video on top
    assert "pad=" not in graph  # no black bars


def test_title_position_maps_to_alignment():
    assert _title_align({}) == 8  # top (default)
    assert _title_align({"title_position": "center"}) == 5
    assert _title_align({"title_position": "bottom"}) == 2


def test_title_position_unknown_raises():
    with pytest.raises(ValueError, match="unknown title_position"):
        _title_align({"title_position": "sideways"})


def test_title_box_uses_border_style_3(tmp_path):
    assert _title_border({}) == (1, "&H00000000")  # plain outline by default
    border, _ = _title_border({"title_box": True})
    assert border == 3  # opaque box
    style = next(
        ln
        for ln in _title_ass({"title": "hi", "title_box": True}, ctx(tmp_path), "t")
        .read_text()
        .splitlines()
        if ln.startswith("Style: Title")
    )
    assert ",3,2,1," in style  # BorderStyle 3 in the style line


def test_muted_bool_applies_to_all():
    assert _muted({"mute": True}, 0) is True
    assert _muted({"mute": True}, 7) is True
    assert _muted({}, 0) is False


def test_muted_list_is_per_clip():
    params = {"mute": [False, True, False]}
    assert _muted(params, 0) is False
    assert _muted(params, 1) is True
    assert _muted(params, 5) is False  # out of range -> not muted


def test_render_cover_and_mute_reach_ffmpeg(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(ffmpeg, "run", lambda args: calls.setdefault("args", args))
    monkeypatch.setattr(ffmpeg, "detect_content_crop", lambda _m: None)  # no source bars
    ExportBlock().execute(
        {"format": "vertical", "fit": "cover", "mute": True, "output": str(tmp_path / "o.mp4")},
        ctx(tmp_path),
        "exp",
    )
    vf = calls["args"][calls["args"].index("-vf") + 1]
    assert "crop=1080:1920" in vf and "pad=" not in vf  # cover, no bars
    assert "-af" in calls["args"] and "volume=0" in calls["args"]  # muted


def test_cover_strips_source_letterbox_bars(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(ffmpeg, "run", lambda args: calls.setdefault("args", args))
    # Source has baked-in bars: real content is 1920x800 at y=140.
    monkeypatch.setattr(ffmpeg, "detect_content_crop", lambda _m: "1920:800:0:140")
    ExportBlock().execute(
        {"format": "vertical", "fit": "cover", "output": str(tmp_path / "o.mp4")},
        ctx(tmp_path),
        "exp",
    )
    vf = calls["args"][calls["args"].index("-vf") + 1]
    # the source crop is applied BEFORE the fill+crop, so the bars are gone
    assert vf.startswith("crop=1920:800:0:140,")
    assert "force_original_aspect_ratio=increase" in vf


def test_cover_trim_bars_can_be_disabled(tmp_path, monkeypatch):
    seen = {"detect": 0}

    def fake_detect(_m):
        seen["detect"] += 1
        return "1920:800:0:140"

    monkeypatch.setattr(ffmpeg, "run", lambda args: None)
    monkeypatch.setattr(ffmpeg, "detect_content_crop", fake_detect)
    params = {
        "format": "vertical",
        "fit": "cover",
        "trim_bars": False,
        "output": str(tmp_path / "o.mp4"),
    }
    ExportBlock().execute(params, ctx(tmp_path), "exp")
    assert seen["detect"] == 0  # detection skipped when trim_bars: false


def test_export_renders_and_lists_file(tmp_path, monkeypatch):
    def fake_run(args):
        # last arg is the output path; create it so the result is realistic.
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    out = (
        ExportBlock()
        .execute({"format": "vertical", "output": str(tmp_path / "o.mp4")}, ctx(tmp_path), "exp")
        .outputs
    )
    assert out["files"] == [str(tmp_path / "o.mp4")]
    assert (tmp_path / "o.mp4").exists()
