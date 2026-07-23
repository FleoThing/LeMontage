"""Tests for the `music` block (FFmpeg mocked)."""

from pathlib import Path

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.music import MusicBlock
from lemontage.engine.context import RunContext


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """Stub ffmpeg + a real music file; return the captured run() args."""
    calls = {}

    def fake_run(args):
        calls["args"] = args
        Path(args[-1]).write_bytes(b"v")

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda p: 10.0)
    monkeypatch.setattr(ffmpeg, "has_audio", lambda p: True)
    (tmp_path / "track.mp3").write_bytes(b"a")
    return calls


def test_music_muxes_over_channel_reel(tmp_path, stubbed):
    src = str(tmp_path / "track.mp3")
    res = MusicBlock().execute_channel(
        {"source": src, "fade_out": "2s"},
        [{"index": 0, "file": "reel.mp4"}],
        ctx(tmp_path),
        "bgm",
    )
    args = stubbed["args"]
    graph = args[args.index("-filter_complex") + 1]
    assert "afade=t=out:st=8.000:d=2.000" in graph
    assert "amix=inputs=2" in graph
    assert res.outputs["file"].endswith("demo-music.mp4")
    assert res.channel_items[0]["file"] == res.outputs["file"]


def test_music_no_video_audio_maps_music_only(tmp_path, stubbed, monkeypatch):
    monkeypatch.setattr(ffmpeg, "has_audio", lambda p: False)
    MusicBlock().execute_channel(
        {"source": str(tmp_path / "track.mp3")},
        [{"index": 0, "file": "reel.mp4"}],
        ctx(tmp_path),
        "bgm",
    )
    args = stubbed["args"]
    assert "[m]" in args  # music mapped directly, no amix
    assert "amix" not in args[args.index("-filter_complex") + 1]


def test_music_mix_false_ignores_video_audio(tmp_path, stubbed):
    # Even when the video HAS audio, mix:false must drop it and map music only —
    # so muted, concat-spliced silent tracks can't stutter the music.
    MusicBlock().execute_channel(
        {"source": str(tmp_path / "track.mp3"), "mix": False},
        [{"index": 0, "file": "reel.mp4"}],
        ctx(tmp_path),
        "bgm",
    )
    args = stubbed["args"]
    assert "[m]" in args
    assert "amix" not in args[args.index("-filter_complex") + 1]


def test_music_rejects_multi_clip_channel(tmp_path, stubbed):
    with pytest.raises(ValueError, match="concat"):
        MusicBlock().execute_channel(
            {"source": str(tmp_path / "track.mp3")},
            [{"index": 0, "file": "a.mp4"}, {"index": 1, "file": "b.mp4"}],
            ctx(tmp_path),
            "bgm",
        )


def test_music_requires_source(tmp_path, stubbed):
    with pytest.raises(ValueError, match="source"):
        MusicBlock().execute({}, ctx(tmp_path), "bgm")


def test_music_missing_source_file(tmp_path, stubbed):
    with pytest.raises(ValueError, match="not found"):
        MusicBlock().execute({"source": str(tmp_path / "nope.mp3")}, ctx(tmp_path), "bgm")


def test_music_loops_short_track(tmp_path, stubbed, monkeypatch):
    # music (3s) shorter than video (10s) -> -stream_loop -1 on the music input
    monkeypatch.setattr(
        ffmpeg, "probe_duration", lambda p: 3.0 if str(p).endswith(".mp3") else 10.0
    )
    MusicBlock().execute({"source": str(tmp_path / "track.mp3")}, ctx(tmp_path), "bgm")
    assert "-stream_loop" in stubbed["args"]


# --- timing: start_at (skip into track) / delay (enter later) ----------------


def test_start_at_skips_into_track(tmp_path, stubbed):
    # start_at trims 8s off the front of the music
    MusicBlock().execute(
        {"source": str(tmp_path / "track.mp3"), "start_at": "8s"}, ctx(tmp_path), "bgm"
    )
    graph = stubbed["args"][stubbed["args"].index("-filter_complex") + 1]
    assert "atrim=start=8.000" in graph


def test_delay_pushes_music_later(tmp_path, stubbed):
    # delay pads silence in front so the music enters 4s into the video (adelay),
    # and only fills the remaining video_dur - delay = 6s
    MusicBlock().execute(
        {"source": str(tmp_path / "track.mp3"), "delay": "4s"}, ctx(tmp_path), "bgm"
    )
    graph = stubbed["args"][stubbed["args"].index("-filter_complex") + 1]
    assert "adelay=4000:all=1" in graph
    assert "atrim=duration=6.000" in graph


def test_no_timing_fills_whole_video(tmp_path, stubbed):
    MusicBlock().execute({"source": str(tmp_path / "track.mp3")}, ctx(tmp_path), "bgm")
    graph = stubbed["args"][stubbed["args"].index("-filter_complex") + 1]
    assert "atrim=duration=10.000" in graph
    assert "adelay" not in graph
