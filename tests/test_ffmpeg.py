"""Tests for the FFmpeg/FFprobe wrapper.

The binary is never actually invoked: ``subprocess.run`` and the binary locator
are stubbed so the wrapper's own logic (argument assembly, return-code handling,
stderr parsing) is what gets exercised.
"""

import pytest

from lemontage.engine import ffmpeg


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- run / run_capture ------------------------------------------------------


def test_run_raises_ffmpeg_error_on_nonzero(monkeypatch):
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(
        ffmpeg.subprocess, "run", lambda *a, **k: FakeProc(returncode=1, stderr="bad filter")
    )
    with pytest.raises(ffmpeg.FFmpegError, match="bad filter"):
        ffmpeg.run(["-i", "x.mp4"])


def test_run_prepends_y_and_error_loglevel(monkeypatch):
    captured = {}

    def fake(cmd, **k):
        captured["cmd"] = cmd
        return FakeProc(returncode=0)

    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "FFMPEG")
    monkeypatch.setattr(ffmpeg.subprocess, "run", fake)
    ffmpeg.run(["-i", "x.mp4"])
    assert captured["cmd"][0] == "FFMPEG"
    assert "-y" in captured["cmd"] and "error" in captured["cmd"]


def test_run_capture_returns_stderr(monkeypatch):
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(
        ffmpeg.subprocess, "run", lambda *a, **k: FakeProc(stderr="silence_end: 1.0")
    )
    assert "silence_end" in ffmpeg.run_capture(["-i", "x"])


# --- duration parsing -------------------------------------------------------


def test_parse_duration_reads_hms():
    assert ffmpeg._parse_duration("  Duration: 00:01:02.50, start: 0.0\n") == pytest.approx(62.5)


def test_parse_duration_no_match_raises():
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg._parse_duration("no duration here")


def test_probe_duration_uses_ffprobe_when_present(monkeypatch):
    monkeypatch.setattr(
        ffmpeg.shutil, "which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None
    )
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *a, **k: FakeProc(stdout="12.5\n"))
    assert ffmpeg.probe_duration("x.mp4") == pytest.approx(12.5)


def test_probe_duration_falls_back_to_ffmpeg_stderr(monkeypatch):
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)  # no ffprobe
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(
        ffmpeg.subprocess, "run", lambda *a, **k: FakeProc(stderr="Duration: 00:00:03.00,")
    )
    assert ffmpeg.probe_duration("x.mp4") == pytest.approx(3.0)


# --- resolution parsing -----------------------------------------------------


def test_probe_resolution_parses_wxh(monkeypatch):
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(
        ffmpeg.subprocess,
        "run",
        lambda *a, **k: FakeProc(stderr="Stream #0:0: Video: h264, yuv420p, 1920x1080, 30 fps"),
    )
    assert ffmpeg.probe_resolution("x.mp4") == (1920, 1080)


def test_probe_resolution_no_match_raises(monkeypatch):
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *a, **k: FakeProc(stderr="audio only"))
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.probe_resolution("x.mp4")


# --- binary locator ---------------------------------------------------------


def test_ffmpeg_bin_prefers_system(monkeypatch):
    ffmpeg.ffmpeg_bin.cache_clear()
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    try:
        assert ffmpeg.ffmpeg_bin() == "/usr/bin/ffmpeg"
    finally:
        ffmpeg.ffmpeg_bin.cache_clear()
