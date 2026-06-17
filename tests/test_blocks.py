"""Tests for the native blocks.

Heavy backends (FFmpeg, Whisper, kokoro) are mocked; these tests pin the block
logic — output shapes, path naming, SRT generation, clip windowing.
"""

from pathlib import Path

import pytest

from reelflow.engine import ffmpeg, providers
from reelflow.engine.blocks.captions import CaptionsBlock, _pack_words, _write_srt
from reelflow.engine.blocks.detect_clips import _select_loud_clips, _windowed_clips
from reelflow.engine.blocks.export import (
    ExportBlock,
    _output_path,
    _target_size,
    _title_ass,
)
from reelflow.engine.blocks.stt import SttBlock
from reelflow.engine.blocks.tts import TtsBlock
from reelflow.engine.context import RunContext
from reelflow.engine.providers.base import Segment, Transcript, TTSResult


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
                segments=[Segment(0.0, 1.0, "hello"), Segment(1.0, 2.0, "world")],
                lang="en",
            )

    monkeypatch.setattr(providers, "default_stt", lambda model="base": FakeSTT())
    out = SttBlock().execute({"model": "base", "lang": "en"}, ctx(tmp_path), "t").outputs
    assert out["text"] == "hello world"
    assert out["lang"] == "en"
    assert out["segments"][0] == {"start": 0.0, "end": 1.0, "text": "hello"}


def test_stt_requires_media(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "default_stt", lambda model="base": None)
    with pytest.raises(ValueError):
        SttBlock().execute({}, ctx(tmp_path, input={}), "t")


# --- tts -------------------------------------------------------------------


def test_tts_writes_audio_output(tmp_path, monkeypatch):
    class FakeTTS:
        def synthesize(self, text, out_path, voice="default", speed=1.0):
            Path(out_path).write_bytes(b"x")
            return TTSResult(audio=Path(out_path), duration=1.23)

    monkeypatch.setattr(providers, "default_tts", lambda: FakeTTS())
    out = TtsBlock().execute({"text": "hi"}, ctx(tmp_path), "voice").outputs
    assert out["duration"] == 1.23
    assert out["audio"].endswith("voice.wav")


def test_tts_requires_text(tmp_path):
    with pytest.raises(ValueError):
        TtsBlock().execute({}, ctx(tmp_path), "voice")


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
    (start, end), = _select_loud_clips(
        _timeline([(40, 50)]), total=200.0, min_dur=8, max_dur=30, max_clips=1
    )
    assert round(start) == 37  # onset 40 - 3s lead-in
    assert round(end - start) == 30  # fills the requested length forward
    assert start <= 40 and end >= 50  # the loud moment is inside


def test_select_loud_clips_pads_to_min_duration_near_end():
    # Near the end of the media the forward window is short -> padded to min_dur.
    (start, end), = _select_loud_clips(
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


# --- captions SRT ----------------------------------------------------------


def test_write_srt_shifts_and_filters_by_offset(tmp_path):
    segments = [
        {"start": 1.0, "end": 2.0, "text": "before"},  # before clip -> dropped
        {"start": 11.0, "end": 13.0, "text": "inside"},
    ]
    srt, count = _write_srt(segments, offset=10.0, path=tmp_path / "x.srt", max_chars=42)
    text = srt.read_text()
    assert count == 1
    assert "inside" in text
    assert "before" not in text
    # 11s - 10s offset -> 1s, formatted with comma decimals.
    assert "00:00:01,000 --> 00:00:03,000" in text


def test_write_srt_empty_when_no_segments_in_window(tmp_path):
    _, count = _write_srt(
        [{"start": 1.0, "end": 2.0, "text": "x"}], offset=100.0, path=tmp_path / "e.srt", max_chars=42
    )
    assert count == 0


def test_pack_words_respects_max_chars():
    chunks = _pack_words("the quick brown fox jumps over the lazy dog", max_chars=15)
    assert all(len(c) <= 15 for c in chunks)
    assert " ".join(chunks) == "the quick brown fox jumps over the lazy dog"


def test_write_srt_splits_long_segment_into_several_cues(tmp_path):
    seg = [{"start": 0.0, "end": 8.0, "text": "one two three four five six seven eight nine ten"}]
    _, count = _write_srt(seg, offset=0.0, path=tmp_path / "s.srt", max_chars=20)
    assert count >= 2  # a long line is broken into multiple short cues


def test_stt_forwards_vad_and_beam_options(tmp_path, monkeypatch):
    captured = {}

    class FakeSTT:
        def transcribe(self, media, lang="auto", **options):
            captured.update(options)
            return Transcript(text="", segments=[], lang="en")

    monkeypatch.setattr(providers, "default_stt", lambda model="base": FakeSTT())
    SttBlock().execute({"vad_filter": True, "beam_size": 3}, ctx(tmp_path), "t")
    assert captured == {"vad_filter": True, "beam_size": 3}


def test_captions_requires_segments_list(tmp_path):
    with pytest.raises(ValueError):
        CaptionsBlock().execute({"segments": "nope"}, ctx(tmp_path), "c")


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
    from reelflow.engine import ffmpeg as ff
    from reelflow.engine.blocks.concat import ConcatBlock

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
    from reelflow.engine.blocks.concat import ConcatBlock

    with pytest.raises(ValueError):
        ConcatBlock().execute({}, ctx(tmp_path), "concat")


def test_export_renders_and_lists_file(tmp_path, monkeypatch):
    def fake_run(args):
        # last arg is the output path; create it so the result is realistic.
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    out = ExportBlock().execute(
        {"format": "vertical", "output": str(tmp_path / "o.mp4")}, ctx(tmp_path), "exp"
    ).outputs
    assert out["files"] == [str(tmp_path / "o.mp4")]
    assert (tmp_path / "o.mp4").exists()
