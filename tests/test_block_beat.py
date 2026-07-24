"""Tests for `detect_clips: method: beat` (librosa mocked — not installed in CI)."""

from lemontage.engine import ffmpeg
from lemontage.engine.blocks import detect_clips
from lemontage.engine.blocks.detect_clips import DetectClipsBlock, _beat_clips
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


def test_beat_clips_cuts_land_on_beats():
    """Clip durations follow the grouped beat spacing (so the reel cuts on the
    beat), and clips are drawn from spread-out, distinct source positions (so each
    cut is a visible jump, not continuous footage)."""
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    clips = _beat_clips(beats, total=30.0, beats_per_clip=2, max_clips=5)
    durations = [round(e - s, 3) for s, e in clips]
    assert durations == [1.0, 1.0, 1.0]  # every 2 beats = 1.0s → beat-locked rhythm
    starts = [s for s, _ in clips]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)  # spread, distinct
    assert starts[1] - starts[0] > 1.0  # each clip jumps further than its own length


def test_beat_clips_start_at_source_offset():
    """source_start starts the montage past an intro handled by another channel."""
    beats = [0.0, 1.0, 2.0, 3.0, 4.0]
    clips = _beat_clips(beats, total=100.0, beats_per_clip=1, max_clips=5, source_start=8.0)
    assert clips[0][0] == 8.0  # first clip begins at the offset, not 0
    assert all(s >= 8.0 for s, _ in clips)  # nothing drawn from before the intro
    assert [round(e - s, 3) for s, e in clips] == [1.0, 1.0, 1.0, 1.0]  # beat-spaced


def test_beat_clips_caps_at_max_clips():
    beats = [i * 0.5 for i in range(40)]
    assert len(_beat_clips(beats, total=999.0, beats_per_clip=1, max_clips=4)) == 4


def test_beat_method_emits_beat_aligned_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda p: 30.0)
    monkeypatch.setattr(
        detect_clips, "_beat_times", lambda track, start_at: [0.0, 1.0, 2.0, 3.0, 4.0]
    )
    (tmp_path / "track.mp3").write_bytes(b"a")

    res = DetectClipsBlock().execute(
        {"method": "beat", "track": str(tmp_path / "track.mp3"), "beats_per_clip": 1, "emit": "m"},
        ctx(tmp_path),
        "grid",
    )
    durations = [round(it["end"] - it["start"], 3) for it in res.channel_items]
    assert durations == [1.0, 1.0, 1.0, 1.0]  # beat-locked cut rhythm
    starts = [it["start"] for it in res.channel_items]
    assert len(set(starts)) == len(starts)  # distinct source moments → visible cuts
    assert res.outputs["beats"] == [0.0, 1.0, 2.0, 3.0, 4.0]  # grid exposed to the agent loop


def test_validator_beat_requires_track():
    doc = {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [{"id": "g", "detect_clips": {"method": "beat", "emit": "m"}}],
    }
    assert any("requires a 'track'" in e for e in validate_doc(doc))
