"""Tests for the Reelflow v1 pipeline validator."""

import copy

import pytest

from reelflow.validator import validate_doc, validate_file

VALID_PIPELINE = {
    "reelflow": "1.0",
    "name": "podcast-to-clips",
    "input": {"type": "video", "source": "./video-example.mp4"},
    "steps": [
        {"id": "transcript", "stt": {"model": "base", "lang": "fr"}},
        {"id": "clips", "detect_clips": {"max_clips": 5, "emit": "clip_channel"}},
        {"cut": {"from": "clip_channel"}},
        {"captions": {"from": "clip_channel", "style": "tiktok"}},
        {"export": {"from": "clip_channel", "format": "vertical"}},
    ],
    "output": {"dir": "./output"},
}


def doc_without(**overrides):
    d = copy.deepcopy(VALID_PIPELINE)
    d.update(overrides)
    return d


def test_valid_pipeline_passes():
    assert validate_doc(VALID_PIPELINE) == []


def test_top_level_must_be_mapping():
    assert validate_doc(["not", "a", "mapping"])


@pytest.mark.parametrize("key", ["reelflow", "name", "input", "steps"])
def test_missing_required_key(key):
    d = copy.deepcopy(VALID_PIPELINE)
    del d[key]
    errors = validate_doc(d)
    assert any(key in e for e in errors)


def test_unsupported_version():
    errors = validate_doc(doc_without(reelflow="2.0"))
    assert any("unsupported spec version" in e for e in errors)


def test_version_must_be_string():
    errors = validate_doc(doc_without(reelflow=1.0))
    assert any("must be a string" in e for e in errors)


def test_unknown_top_level_key():
    errors = validate_doc(doc_without(banana=True))
    assert any("unknown top-level key" in e for e in errors)


def test_reserved_top_level_hooks():
    errors = validate_doc(doc_without(hooks={"on_error": []}))
    assert any("hooks" in e and "reserved" in e for e in errors)


def test_reserved_input_type():
    d = doc_without(input={"type": "url", "source": "https://x/y.mp4"})
    errors = validate_doc(d)
    assert any("reserved" in e for e in errors)


def test_non_mp4_input_rejected():
    d = doc_without(input={"type": "video", "source": "./clip.mov"})
    errors = validate_doc(d)
    assert any(".mp4" in e for e in errors)


def test_empty_steps_rejected():
    errors = validate_doc(doc_without(steps=[]))
    assert any("non-empty list" in e for e in errors)


def test_step_with_two_blocks_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"stt": {}, "export": {}}]
    errors = validate_doc(d)
    assert any("exactly one block" in e for e in errors)


def test_unknown_block_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"transmogrify": {}}]
    errors = validate_doc(d)
    assert any("unknown block" in e for e in errors)


def test_reserved_block_music_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"stt": {}}, {"music": {"mood": "calm"}}]
    errors = validate_doc(d)
    assert any("music" in e and "reserved" in e for e in errors)


def test_reserved_detect_method_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"detect_clips": {"method": "engagement", "emit": "c"}}, {"cut": {"from": "c"}}]
    errors = validate_doc(d)
    assert any("engagement" in e for e in errors)


def test_cloud_provider_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"tts": {"text": "hi", "engine": "elevenlabs"}}]
    errors = validate_doc(d)
    assert any("reserved for a later phase" in e for e in errors)


def test_unknown_channel_reference_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"cut": {"from": "ghost_channel"}}]
    errors = validate_doc(d)
    assert any("unknown channel" in e for e in errors)


def test_invalid_on_failure():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"stt": {}, "on_failure": "explode"}]
    errors = validate_doc(d)
    assert any("on_failure" in e for e in errors)


def test_validate_file_missing(tmp_path):
    errors = validate_file(tmp_path / "nope.yaml")
    assert any("file not found" in e for e in errors)


def test_validate_file_bad_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("steps: [unclosed\n", encoding="utf-8")
    errors = validate_file(bad)
    assert any("invalid YAML" in e for e in errors)


def test_example_pipeline_is_valid():
    """The shipped example must validate."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "examples" / "podcast-to-clips.yaml"
    assert validate_file(example) == []
