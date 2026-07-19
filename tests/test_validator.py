"""Tests for the LeMontage v1 pipeline validator."""

import copy

import pytest

from lemontage.validator import validate_doc, validate_file

VALID_PIPELINE = {
    "lemontage": "1.0",
    "name": "clips",
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


@pytest.mark.parametrize("key", ["lemontage", "name", "input", "steps"])
def test_missing_required_key(key):
    d = copy.deepcopy(VALID_PIPELINE)
    del d[key]
    errors = validate_doc(d)
    assert any(key in e for e in errors)


def test_unsupported_version():
    errors = validate_doc(doc_without(lemontage="2.0"))
    assert any("unsupported spec version" in e for e in errors)


def test_version_must_be_string():
    errors = validate_doc(doc_without(lemontage=1.0))
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


def test_reserved_block_tts_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"stt": {}}, {"tts": {"voice": "calm"}}]
    errors = validate_doc(d)
    assert any("tts" in e and "reserved" in e for e in errors)


def test_music_block_accepted():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = list(d["steps"]) + [
        {"concat": {"from": "clip_channel", "emit": "reel"}},
        {
            "music": {
                "from": "reel",
                "source": "track.mp3",
                "start_at": "0s",
                "align": {"drop": "auto", "to": "1.5s"},
                "fade_out": "2s",
            }
        },
    ]
    assert validate_doc(d) == []


def test_music_requires_source():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"music": {"fade_out": "2s"}}]
    errors = validate_doc(d)
    assert any("music requires a 'source'" in e for e in errors)


def test_music_rejects_bad_times_and_align():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {
            "music": {
                "source": "t.mp3",
                "start_at": "abc",
                "fade_out": -1,
                "align": {"drop": "nope", "to": "xyz"},
            }
        }
    ]
    errors = validate_doc(d)
    assert any("music.start_at" in e for e in errors)
    assert any("music.fade_out" in e for e in errors)
    assert any("music.align.drop" in e for e in errors)
    assert any("music.align.to" in e for e in errors)


def test_music_align_must_be_mapping():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"music": {"source": "t.mp3", "align": "auto"}}]
    errors = validate_doc(d)
    assert any("music.align must be a mapping" in e for e in errors)


def test_reserved_detect_method_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"detect_clips": {"method": "engagement", "emit": "c"}}, {"cut": {"from": "c"}}]
    errors = validate_doc(d)
    assert any("engagement" in e for e in errors)


def test_cloud_provider_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"stt": {"engine": "elevenlabs"}}]
    errors = validate_doc(d)
    assert any("reserved for a later phase" in e for e in errors)


def test_reserved_tts_block_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"tts": {"text": "hi"}}]
    errors = validate_doc(d)
    assert any("reserved" in e and "tts" in e for e in errors)


def test_export_valid_fit_and_mute_pass():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "fit": "cover", "mute": [False, True]}}
    assert validate_doc(d) == []


def test_export_unknown_fit_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "fit": "zoom"}}
    errors = validate_doc(d)
    assert any("unknown export fit" in e and "zoom" in e for e in errors)


def test_export_bad_mute_type_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "mute": "yes"}}
    errors = validate_doc(d)
    assert any("mute must be a boolean" in e for e in errors)


def test_concat_valid_transitions_pass():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [
        {
            "concat": {
                "from": "clip_channel",
                "transitions": ["fade", "fadeblack", "zoomin", "circleopen", "dissolve", "radial"],
            }
        }
    ]
    assert validate_doc(d) == []


def test_still_valid_motion_passes():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"still": {"image": "./cover.png", "motion": "zoomout", "motion_amount": 1.2}}]
    assert validate_doc(d) == []


def test_still_unknown_motion_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"still": {"image": "./cover.png", "motion": "spin"}}]
    errors = validate_doc(d)
    assert any("unknown still motion" in e and "spin" in e for e in errors)


def test_still_bad_motion_amount_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"still": {"image": "./cover.png", "motion": "zoomout", "motion_amount": 1}}]
    errors = validate_doc(d)
    assert any("motion_amount" in e for e in errors)


def test_concat_unknown_transition_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"concat": {"from": "clip_channel", "transitions": "zoom"}}]
    errors = validate_doc(d)
    assert any("unknown transition" in e and "zoom" in e for e in errors)


def test_unknown_channel_reference_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"cut": {"from": "ghost_channel"}}]
    errors = validate_doc(d)
    assert any("unknown channel" in e for e in errors)


def test_concat_accepts_channel_list():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"detect_clips": {"emit": "viral"}},
        {"detect_clips": {"method": "silence", "emit": "montage"}},
        {"concat": {"from": ["viral", "montage"]}},
    ]
    assert validate_doc(d) == []


def test_concat_channel_list_reports_unknown_entry():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"detect_clips": {"emit": "viral"}},
        {"concat": {"from": ["viral", "ghost"]}},
    ]
    errors = validate_doc(d)
    assert any("ghost" in e and "unknown channel" in e for e in errors)


def test_concat_transitions_at_boundaries_accepted():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"detect_clips": {"emit": "viral"}},
        {"detect_clips": {"method": "silence", "emit": "montage"}},
        {
            "concat": {
                "from": ["viral", "montage"],
                "transitions": "fade",
                "transitions_at": "boundaries",
            }
        },
    ]
    assert validate_doc(d) == []


def test_concat_transitions_at_invalid_value_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"concat": {"from": "clip_channel", "transitions_at": "sometimes"}}]
    errors = validate_doc(d)
    assert any("transitions_at" in e for e in errors)


def test_mapped_block_rejects_channel_list():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"detect_clips": {"emit": "viral"}},
        {"detect_clips": {"method": "silence", "emit": "montage"}},
        {"cut": {"from": ["viral", "montage"]}},
    ]
    errors = validate_doc(d)
    assert any("does not support a list of channels" in e for e in errors)


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

    example = Path(__file__).resolve().parents[1] / "examples" / "pipeline_clips.yaml"
    assert validate_file(example) == []


def test_multi_channel_example_is_valid():
    """The channel-merge example (concat over a list of channels) must validate."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "examples" / "pipeline_merge.yaml"
    assert validate_file(example) == []
