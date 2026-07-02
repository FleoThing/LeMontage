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
from lemontage.engine.blocks.detect_clips import _select_loud_clips, _windowed_clips
from lemontage.engine.blocks.export import (
    ExportBlock,
    _output_path,
    _target_size,
    _title_ass,
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


# --- concat transitions -----------------------------------------------------


def test_resolve_transitions_string_fills_every_gap():
    from lemontage.engine.blocks.concat import _resolve_transitions

    assert _resolve_transitions({"transitions": "fade"}, 4) == ["fade", "fade", "fade"]


def test_resolve_transitions_list_is_per_gap():
    from lemontage.engine.blocks.concat import _resolve_transitions

    assert _resolve_transitions({"transitions": ["fade", "wipeleft"]}, 3) == ["fade", "wipeleft"]


def test_resolve_transitions_none_everywhere_falls_back_to_plain():
    from lemontage.engine.blocks.concat import _resolve_transitions

    assert _resolve_transitions({}, 3) is None
    assert _resolve_transitions({"transitions": ["none", "none"]}, 3) is None


def test_resolve_transitions_wrong_length_raises():
    from lemontage.engine.blocks.concat import _resolve_transitions

    with pytest.raises(ValueError, match="one per gap"):
        _resolve_transitions({"transitions": ["fade"]}, 3)


def test_resolve_transitions_unknown_name_raises():
    from lemontage.engine.blocks.concat import _resolve_transitions

    with pytest.raises(ValueError, match="unknown transition"):
        _resolve_transitions({"transitions": "zoom"}, 2)


def test_resolve_transitions_single_clip_raises():
    from lemontage.engine.blocks.concat import _resolve_transitions

    with pytest.raises(ValueError, match="only 1 clip"):
        _resolve_transitions({"transitions": "fade"}, 1)


def test_concat_with_transitions_builds_xfade_chain(tmp_path, monkeypatch):
    from lemontage.engine import ffmpeg as ff
    from lemontage.engine.blocks.concat import _concat_with_transitions

    monkeypatch.setattr(ff, "probe_duration", lambda _f: 3.0)
    calls = {}
    monkeypatch.setattr(ff, "run", lambda args: calls.setdefault("args", args))

    files = [str(tmp_path / f"c{i}.mp4") for i in range(3)]
    _concat_with_transitions(files, ["fade", "wipeleft"], 0.5, tmp_path / "reel.mp4")

    graph = calls["args"][calls["args"].index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.5:offset=2.500" in graph
    assert "acrossfade=d=0.5" in graph
    # second gap chains onto the first crossfade's output, offset by another gap
    assert "xfade=transition=wipeleft:duration=0.5:offset=5.000" in graph
    assert calls["args"].count("-i") == 3  # one input per clip


def test_concat_with_transitions_none_gap_uses_hard_cut(tmp_path, monkeypatch):
    from lemontage.engine import ffmpeg as ff
    from lemontage.engine.blocks.concat import _concat_with_transitions

    monkeypatch.setattr(ff, "probe_duration", lambda _f: 3.0)
    calls = {}
    monkeypatch.setattr(ff, "run", lambda args: calls.setdefault("args", args))

    files = [str(tmp_path / f"c{i}.mp4") for i in range(3)]
    _concat_with_transitions(files, ["none", "fade"], 0.5, tmp_path / "reel.mp4")

    graph = calls["args"][calls["args"].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in graph  # the 'none' gap is a hard cut
    assert "xfade=transition=fade" in graph  # the other gap still crossfades


def test_concat_with_transitions_duration_too_long_raises(tmp_path, monkeypatch):
    from lemontage.engine import ffmpeg as ff
    from lemontage.engine.blocks.concat import _concat_with_transitions

    monkeypatch.setattr(ff, "probe_duration", lambda _f: 1.0)
    monkeypatch.setattr(ff, "run", lambda args: None)

    files = [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]
    with pytest.raises(ValueError, match="shorter than both clips"):
        _concat_with_transitions(files, ["fade"], 2.0, tmp_path / "reel.mp4")


def test_concat_block_routes_to_transitions(tmp_path, monkeypatch):
    from lemontage.engine import ffmpeg as ff
    from lemontage.engine.blocks.concat import ConcatBlock

    monkeypatch.setattr(ff, "probe_duration", lambda _f: 3.0)
    calls = {}
    monkeypatch.setattr(ff, "run", lambda args: calls.setdefault("args", args))

    items = [{"index": i, "file": str(tmp_path / f"c{i}.mp4")} for i in range(3)]
    ConcatBlock().execute_channel(
        {"transitions": "fade", "output": str(tmp_path / "reel.mp4")},
        items,
        ctx(tmp_path),
        "concat",
    )
    assert "-filter_complex" in calls["args"]  # took the xfade path, not the demuxer


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
