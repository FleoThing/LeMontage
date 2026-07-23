"""Tests for ``lemontage analyze`` (the VSO manifest).

FFmpeg is mocked at the ``run_capture``/probe boundary so the real parsing
helpers (scene cuts, silencedetect, loudness) run; Whisper is mocked whole.
"""

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
