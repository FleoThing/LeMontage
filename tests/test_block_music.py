"""Tests for the `music` block (FFmpeg mocked) and its drop detection."""

import math
from pathlib import Path

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks import music as music_mod
from lemontage.engine.blocks.music import MusicBlock, _drop_window, _music_offset, detect_drop
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


# --- offset / alignment ------------------------------------------------------


def test_offset_defaults_to_start_at(tmp_path):
    assert _music_offset({"start_at": "5s"}, "t.mp3") == 5.0


def test_offset_align_puts_drop_at_target(monkeypatch, tmp_path):
    # drop at 30s in the music, wanted at 12s in the video -> trim 18s off the front
    assert _music_offset({"align": {"drop": "30s", "to": "12s"}}, "t.mp3") == 18.0


def test_offset_negative_becomes_delay(tmp_path, stubbed):
    # drop at 1s, wanted at 4s -> music starts 3s into the video (adelay)
    MusicBlock().execute(
        {"source": str(tmp_path / "track.mp3"), "align": {"drop": "1s", "to": "4s"}},
        ctx(tmp_path),
        "bgm",
    )
    args = stubbed["args"]
    assert "adelay=3000:all=1" in args[args.index("-filter_complex") + 1]


def test_offset_align_auto_uses_detection(monkeypatch):
    monkeypatch.setattr(music_mod, "detect_drop", lambda src: 20.0)
    assert _music_offset({"align": {"drop": "auto", "to": "5s"}}, "t.mp3") == 15.0


# --- drop detection ----------------------------------------------------------


def test_drop_window_finds_sustained_jump():
    # quiet intro, one spurious spike, then the sustained drop
    rms = [100.0] * 10 + [5000.0] + [100.0] * 9 + [8000.0] * 10
    assert _drop_window(rms) == 20


def test_drop_window_short_signal_returns_zero():
    assert _drop_window([1.0, 2.0]) == 0


def test_detect_drop_on_synthetic_pcm(monkeypatch):
    # 10s quiet sine then 10s loud sine at 8 kHz; drop expected near t=10s
    import array

    rate = 8000
    quiet = [int(500 * math.sin(2 * math.pi * 220 * t / rate)) for t in range(10 * rate)]
    loud = [int(20000 * math.sin(2 * math.pi * 220 * t / rate)) for t in range(10 * rate)]
    monkeypatch.setattr(music_mod, "_decode_pcm", lambda src, r: array.array("h", quiet + loud))
    assert detect_drop("fake.mp3") == pytest.approx(10.0, abs=0.5)
