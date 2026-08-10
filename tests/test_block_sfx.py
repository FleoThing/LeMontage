"""Tests for the `sfx` block (FFmpeg mocked)."""

import pytest

from lemontage.engine import ffmpeg
from lemontage.engine.blocks.sfx import SfxBlock, mix_filter, times_of
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


@pytest.fixture
def captured(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(ffmpeg, "run", lambda args: calls.setdefault("args", args))
    monkeypatch.setattr(ffmpeg, "has_audio", lambda p: True)
    sample = tmp_path / "whoosh.mp3"
    sample.write_bytes(b"fake")
    calls["sample"] = str(sample)
    return calls


def graph_of(calls):
    args = calls["args"]
    return args[args.index("-filter_complex") + 1]


def test_one_hit_needs_no_split(tmp_path, captured):
    SfxBlock().execute({"source": captured["sample"], "at": [0]}, ctx(tmp_path), "s")
    graph = graph_of(captured)
    assert "asplit" not in graph
    assert graph.endswith("amix=inputs=2:duration=first:normalize=0[a]")


def test_several_hits_split_the_sample_and_delay_each(tmp_path, captured):
    SfxBlock().execute({"source": captured["sample"], "at": [0, 1.5, "0:03"]}, ctx(tmp_path), "s")
    graph = graph_of(captured)
    assert graph.startswith("[1:a]asplit=3[s0][s1][s2]")
    assert "adelay=1500:all=1" in graph and "adelay=3000:all=1" in graph
    assert "amix=inputs=4" in graph  # the clip's own audio + 3 hits


def test_mix_never_normalises_so_the_voice_keeps_its_level():
    graph = mix_filter([0.0, 2.0], gain=0, has_audio=True)
    assert "normalize=0" in graph


def test_gain_is_applied_per_hit():
    graph = mix_filter([1.0], gain=-6, has_audio=True)
    assert "volume=-6.00dB" in graph


def test_silent_clip_gets_only_the_effects():
    graph = mix_filter([0.0], gain=0, has_audio=False)
    assert "[0:a]" not in graph and graph.endswith("anull[a]")


def test_times_default_to_the_clip_start_and_are_sorted():
    assert times_of({}) == [0]
    assert times_of({"at": [3, "0:01"]}) == [1, 3]


def test_missing_source_is_a_runtime_error(tmp_path, captured):
    with pytest.raises(ValueError, match="source not found"):
        SfxBlock().execute({"source": "nope.mp3"}, ctx(tmp_path), "s")


def test_mapped_sfx_uses_the_exported_file_when_there_is_one(tmp_path, captured):
    item = {"index": 0, "clip": "cut-0.mp4", "file": "exported-0.mp4"}
    result = SfxBlock().execute_item({"source": captured["sample"]}, item, ctx(tmp_path), "s")
    assert captured["args"][captured["args"].index("-i") + 1] == "exported-0.mp4"
    assert "file" in result.item


def doc(params):
    return {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [{"sfx": params}],
    }


def test_validator_requires_a_source():
    assert any("sfx.source" in e for e in validate_doc(doc({"at": [1]})))


def test_validator_rejects_a_bad_time():
    assert any("is not a time" in e for e in validate_doc(doc({"source": "a.mp3", "at": ["now"]})))


def test_validator_accepts_a_full_sfx_step():
    assert validate_doc(doc({"source": "a.mp3", "at": [0, "0:02"], "gain": -6})) == []
