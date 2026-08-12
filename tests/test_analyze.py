"""Tests for ``lemontage analyze`` (the VSO manifest).

FFmpeg is mocked at the ``run_capture``/probe boundary so the real parsing
helpers (scene cuts, silencedetect, loudness) run; Whisper is mocked whole.
"""

import pytest

from lemontage import analyze


def fake_capture(args):
    """Return canned stderr per analysis filter, keyed on the filter string."""
    joined = " ".join(args)
    if "silencedetect" in joined:
        return "silence_start: 5.0\nsilence_end: 7.0\n"
    if "scene" in joined:  # scene cuts at 4s and 10s
        return "pts_time:4.000\npts_time:10.000\n"
    if "astats" in joined:  # loudness windows, one per shot region
        return (
            "frame pts_time:1.0\nlavfi.astats.Overall.RMS_level=-25.0\n"
            "frame pts_time:6.0\nlavfi.astats.Overall.RMS_level=-15.0\n"
            "frame pts_time:14.0\nlavfi.astats.Overall.RMS_level=-30.0\n"
        )
    return ""


def patch_ffmpeg(monkeypatch, *, audio=True):
    monkeypatch.setattr(analyze.ffmpeg, "probe_duration", lambda _m: 20.0)
    monkeypatch.setattr(analyze.ffmpeg, "probe_fps", lambda _m: 30.0)
    monkeypatch.setattr(analyze.ffmpeg, "has_audio", lambda _m: audio)
    monkeypatch.setattr(analyze.ffmpeg, "run_capture", fake_capture)


def test_manifest_shots_and_loudness(monkeypatch):
    patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(analyze, "_transcribe_words", lambda *a: [])

    m = analyze.analyze_video("v.mp4", transcribe=False)

    assert m["duration"] == 20.0
    assert m["fps"] == 30.0
    assert m["has_audio"] is True
    # scene cuts at 4 and 10 → three shots
    assert [s["id"] for s in m["shots"]] == [1, 2, 3]
    assert m["shots"][0] == {"id": 1, "start": 0.0, "end": 4.0, "loudness_db": -25.0}
    assert m["shots"][1]["loudness_db"] == -15.0  # window at 6s lands in shot 2 (4–10)


def test_dead_air_is_the_silence_gap(monkeypatch):
    patch_ffmpeg(monkeypatch)
    m = analyze.analyze_video("v.mp4", transcribe=False)
    # silence 5–7 → speech (0,5)+(7,20) → dead_air = [[5,7]]
    assert m["speech"]["dead_air"] == [[5.0, 7.0]]
    assert "words" not in m["speech"]  # transcribe=False


def test_words_included_when_transcribing(monkeypatch):
    patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(
        analyze, "_transcribe_words", lambda *a: [{"t": 0.4, "d": 0.4, "w": "Salut"}]
    )
    m = analyze.analyze_video("v.mp4", transcribe=True)
    assert m["speech"]["words"] == [{"t": 0.4, "d": 0.4, "w": "Salut"}]


def test_no_audio_omits_speech(monkeypatch):
    patch_ffmpeg(monkeypatch, audio=False)
    m = analyze.analyze_video("v.mp4")
    assert "speech" not in m
    assert all(s["loudness_db"] is None for s in m["shots"])


def test_apply_normalized_scales_by_max():
    shots = [{"id": 1}, {"id": 2}, {"id": 3}]
    # (sharpness, motion): shot 2 sharpest, shot 3 most motion
    analyze._apply_normalized(shots, [(100.0, 1.0), (400.0, 2.0), (200.0, 4.0)])
    assert shots[1]["sharpness"] == 1.0  # 400/400
    assert shots[0]["sharpness"] == 0.25  # 100/400
    assert shots[2]["motion"] == 1.0  # 4/4
    assert shots[0]["motion"] == 0.25  # 1/4


def test_visual_flag_attaches_scores(monkeypatch):
    patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(analyze, "_transcribe_words", lambda *a: [])

    def fake_visual(_path, shots, samples=4):
        for s in shots:
            s["sharpness"], s["motion"] = 0.5, 0.5

    monkeypatch.setattr(analyze, "_visual_scores", fake_visual)
    m = analyze.analyze_video("v.mp4", transcribe=False, visual=True)
    assert all("sharpness" in s and "motion" in s for s in m["shots"])


def test_visual_raises_when_no_frames_decode(monkeypatch):
    """OpenCV yields no frames for codecs it can't decode (e.g. AV1) — fail loudly
    instead of emitting all-zero scores."""
    cv2 = pytest.importorskip("cv2")

    class FakeCap:
        def isOpened(self):
            return True

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: FakeCap())
    monkeypatch.setattr(analyze, "_sample_gray", lambda *a, **k: [])  # nothing decodes
    with pytest.raises(RuntimeError, match="decoded no frames"):
        analyze._visual_scores("av1.mp4", [{"start": 0.0, "end": 1.0}])


# -------- Packed phrase view -------------------------------------------------


def w(t, d, text):
    return {"t": t, "d": d, "w": text}


def test_pack_phrases_breaks_on_long_silence():
    words = [w(0.0, 0.4, "one"), w(0.5, 0.4, "two"), w(2.0, 0.4, "three")]
    phrases = analyze.pack_phrases(words)

    assert [p["text"] for p in phrases] == ["one two", "three"]
    # Boundaries stay on word edges so they can be replayed as agent spans.
    assert phrases[0] == {"start": 0.0, "end": 0.9, "text": "one two"}
    assert phrases[1]["start"] == 2.0


def test_pack_phrases_keeps_words_under_the_gap_together():
    # 0.49s gap — just under the 0.5s threshold, must not split.
    words = [w(0.0, 0.1, "a"), w(0.59, 0.1, "b")]
    assert len(analyze.pack_phrases(words)) == 1


def test_pack_phrases_handles_no_speech():
    assert analyze.pack_phrases([]) == []


def test_format_packed_reports_silent_track():
    out = analyze.format_packed([("clip", {"duration": 12.0, "speech": {"words": []}})])
    assert "## clip  (12.0s, 0 phrases)" in out
    assert "no speech" in out


def test_format_packed_renders_ranges():
    manifest = {"duration": 3.0, "speech": {"words": [w(1.0, 0.5, "hi"), w(1.4, 0.2, "there")]}}
    out = analyze.format_packed([("take1", manifest)])
    assert "[0001.00-0001.60] hi there" in out


# -- the three ffmpeg passes overlap (analyze._analysis_passes) ----------------


def test_analysis_passes_run_concurrently(monkeypatch):
    """Scene cuts, loudness and silence are independent: they must not queue.

    The check is the peak number of passes in flight at once, not the wall
    clock. Three passes all inside `run_capture` simultaneously *is* the
    property, and it is the stronger evidence: a duration under some threshold
    can be met on a fast machine even when two of the three queued up. Timing
    the run instead measures how loaded the CI runner is, which is how this
    test used to fail on Windows while still observing all three in flight.
    """
    import threading
    import time

    running = set()
    peak = 0
    lock = threading.Lock()

    def slow_capture(args):
        nonlocal peak
        with lock:
            running.add(threading.get_ident())
            peak = max(peak, len(running))
        time.sleep(0.15)
        with lock:
            running.discard(threading.get_ident())
        return fake_capture(args)

    patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(analyze.ffmpeg, "run_capture", slow_capture)
    monkeypatch.setattr(analyze, "_transcribe_words", lambda *a: [])

    analyze.analyze_video("v.mp4", transcribe=False)

    assert peak == 3, f"expected all three passes in flight at once, saw {peak}"


def test_analysis_passes_skip_audio_work_on_a_silent_file(monkeypatch):
    """No audio track means no loudness and no silencedetect pass at all."""
    seen = []

    def recording_capture(args):
        seen.append(" ".join(args))
        return fake_capture(args)

    patch_ffmpeg(monkeypatch, audio=False)
    monkeypatch.setattr(analyze.ffmpeg, "run_capture", recording_capture)

    manifest = analyze.analyze_video("v.mp4", transcribe=False)

    assert "speech" not in manifest
    assert not any("astats" in call or "silencedetect" in call for call in seen), seen
    assert any("scene" in call for call in seen), seen


def test_a_failing_pass_still_raises(monkeypatch):
    """A pass that blows up in a worker thread must not be swallowed."""

    def exploding_capture(args):
        if "scene" in " ".join(args):
            raise RuntimeError("ffmpeg exploded")
        return fake_capture(args)

    patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(analyze.ffmpeg, "run_capture", exploding_capture)

    with pytest.raises(RuntimeError, match="ffmpeg exploded"):
        analyze.analyze_video("v.mp4", transcribe=False)
