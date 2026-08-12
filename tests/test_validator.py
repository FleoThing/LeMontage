"""Tests for the LeMontage v1 pipeline validator."""

import copy
from pathlib import Path

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


def test_duplicate_explicit_ids_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"id": "same", "stt": {}},
        {"id": "same", "cut": {"start": "0s", "end": "1s"}},
    ]
    errors = validate_doc(d)
    assert any("duplicate step id 'same'" in e for e in errors)


def test_duplicate_anonymous_same_block_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"cut": {"start": "0s", "end": "1s"}},
        {"cut": {"start": "1s", "end": "2s"}},
    ]
    errors = validate_doc(d)
    assert any("duplicate step id 'cut'" in e and "distinct 'id:'" in e for e in errors)


def test_anonymous_step_colliding_with_explicit_id_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"id": "cut", "stt": {}},
        {"cut": {"start": "0s", "end": "1s"}},
    ]
    errors = validate_doc(d)
    assert any("duplicate step id 'cut'" in e for e in errors)


def test_distinct_ids_pass():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"id": "intro", "cut": {"start": "0s", "end": "1s"}},
        {"id": "outro", "cut": {"start": "1s", "end": "2s"}},
    ]
    assert validate_doc(d) == []


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
                "delay": "1s",
                "fade_out": "2s",
            }
        },
    ]
    assert validate_doc(d) == []


def compose_doc(compose_params):
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"id": "comp", "compose": compose_params}]
    return d


def test_compose_block_accepted():
    d = compose_doc(
        {
            "format": "vertical",
            "layers": [
                {"image": "bg.jpg", "fit": "cover"},
                {
                    "video": "person.mp4",
                    "y": "25%",
                    "height": "50%",
                    "fit": "contain",
                    "key": {"color": "green"},
                    "on_short": "loop",
                },
            ],
            "audio": 1,
            "emit": "reel",
        }
    )
    assert validate_doc(d) == []


def test_compose_requires_layers():
    errors = validate_doc(compose_doc({"format": "vertical"}))
    assert any("compose requires a non-empty 'layers'" in e for e in errors)


def test_compose_layer_needs_a_source():
    errors = validate_doc(compose_doc({"layers": [{"x": 0}]}))
    assert any("needs a 'video' or an 'image'" in e for e in errors)


def test_compose_layer_cannot_be_both_video_and_image():
    errors = validate_doc(compose_doc({"layers": [{"video": "a.mp4", "image": "b.jpg"}]}))
    assert any("pick one" in e for e in errors)


@pytest.mark.parametrize("extent", ["half", "fifty%", "abc%"])
def test_compose_rejects_unreadable_geometry(extent):
    errors = validate_doc(compose_doc({"layers": [{"image": "bg.jpg", "width": extent}]}))
    assert any("percentage" in e for e in errors)


@pytest.mark.parametrize("good", [540, "50%", "33.5%", -40])
def test_compose_accepts_pixels_and_percentages(good):
    assert validate_doc(compose_doc({"layers": [{"image": "bg.jpg", "x": good}]})) == []


def test_compose_rejects_unknown_fit_and_on_short():
    errors = validate_doc(
        compose_doc({"layers": [{"video": "a.mp4", "fit": "squish", "on_short": "stretch"}]})
    )
    assert any(".fit must be one of" in e for e in errors)
    assert any(".on_short must be one of" in e for e in errors)


def test_compose_rejects_an_audio_layer_that_does_not_exist():
    errors = validate_doc(compose_doc({"layers": [{"image": "bg.jpg"}], "audio": 3}))
    assert any("compose.audio" in e for e in errors)


@pytest.mark.parametrize("audio", ["mix", "none", 0])
def test_compose_accepts_the_audio_selectors(audio):
    assert validate_doc(compose_doc({"layers": [{"video": "a.mp4"}], "audio": audio})) == []


def test_music_requires_source():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [{"music": {"fade_out": "2s"}}]
    errors = validate_doc(d)
    assert any("music requires a 'source'" in e for e in errors)


def test_music_rejects_bad_times():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {
            "music": {
                "source": "t.mp3",
                "start_at": "abc",
                "delay": -1,
                "fade_out": -1,
            }
        }
    ]
    errors = validate_doc(d)
    assert any("music.start_at" in e for e in errors)
    assert any("music.delay" in e for e in errors)
    assert any("music.fade_out" in e for e in errors)


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


def test_export_valid_canvas_and_position_pass():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "canvas": "1080x1920", "position": "top"}}
    assert validate_doc(d) == []


def test_export_bad_canvas_format_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "canvas": "1080by1920"}}
    errors = validate_doc(d)
    assert any("export.canvas" in e and "WIDTHxHEIGHT" in e for e in errors)


def test_export_xy_position_passes():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {"export": {"from": "clip_channel", "canvas": "720x1280", "position": "0,421"}}
    assert validate_doc(d) == []


def test_export_unknown_position_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"][-1] = {
        "export": {"from": "clip_channel", "canvas": "1080x1920", "position": "corner"}
    }
    errors = validate_doc(d)
    assert any("unknown export position" in e and "corner" in e for e in errors)


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
        {"id": "viral-clips", "detect_clips": {"emit": "viral"}},
        {"id": "montage-clips", "detect_clips": {"method": "silence", "emit": "montage"}},
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
        {"id": "viral-clips", "detect_clips": {"emit": "viral"}},
        {"id": "montage-clips", "detect_clips": {"method": "silence", "emit": "montage"}},
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
        {"id": "viral-clips", "detect_clips": {"emit": "viral"}},
        {"id": "montage-clips", "detect_clips": {"method": "silence", "emit": "montage"}},
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


EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.yaml"))


def test_examples_directory_is_not_empty():
    """Guard the guard: a bad glob would make the test below vacuously pass."""
    assert len(EXAMPLES) >= 15, EXAMPLES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_every_shipped_example_is_valid(example):
    """Every example in examples/ must validate.

    Only two of them used to be checked, by name. The other eighteen were never
    read by anything, and two had gone invalid without anyone noticing: when the
    duplicate-step-id rule landed, `pipeline_carousel.yaml` and
    `pipeline_transition.yaml` kept anonymous steps that collide on their
    default id. A parametrised sweep costs nothing and would have caught it the
    day the rule was added.
    """
    assert validate_file(example) == []


def test_concat_single_transition_accepted():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"id": "p1", "detect_clips": {"emit": "part1"}},
        {"id": "p2", "detect_clips": {"method": "silence", "emit": "part2"}},
        {
            "concat": {
                "from": ["part1", "part2"],
                "transition": {"type": "fadewhite", "duration": "0.5s", "at": "11s"},
            }
        },
    ]
    assert validate_doc(d) == []


def test_concat_single_transition_unknown_type_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"concat": {"from": "clip_channel", "transition": {"type": "swirl"}}}]
    errors = validate_doc(d)
    assert any("unknown transition type" in e and "swirl" in e for e in errors)


def test_concat_single_transition_must_be_mapping():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [{"concat": {"from": "clip_channel", "transition": "fade"}}]
    errors = validate_doc(d)
    assert any("must be a mapping" in e for e in errors)


def test_concat_single_transition_conflicts_with_transitions():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [
        {
            "concat": {
                "from": "clip_channel",
                "transition": {"type": "fade"},
                "transitions": "fade",
            }
        }
    ]
    errors = validate_doc(d)
    assert any("not both" in e for e in errors)


def test_concat_single_transition_bad_at_rejected():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [
        {"concat": {"from": "clip_channel", "transition": {"type": "fade", "at": "nope"}}}
    ]
    errors = validate_doc(d)
    assert any("transition.at" in e for e in errors)


def test_concat_new_xfade_types_accepted():
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] += [
        {
            "concat": {
                "from": "clip_channel",
                "transitions": ["fadewhite", "fadegrays", "pixelize", "distance", "smoothleft"],
            }
        }
    ]
    # 5 names for however many gaps -- count is a runtime check, names must pass.
    errors = [e for e in validate_doc(d) if "unknown transition" in e]
    assert errors == []


# -- per-block dispatch (validator._BLOCK_CHECKS) ------------------------------
#
# _check_block_params dispatches on the block name through a dict. A typo in a
# key, or a handler left out of the table, would silently stop validating a whole
# block -- pipelines would then fail at render time instead of at `validate`.
# These tests pin the wiring: one accepted doc and one rejected doc per block.


def _with_step(block, params, *, extra_steps=()):
    d = copy.deepcopy(VALID_PIPELINE)
    d["steps"] = [
        {"id": "clips", "detect_clips": {"max_clips": 2, "emit": "clip_channel"}},
        *extra_steps,
        {"id": "under-test", block: params},
    ]
    return d


# (block, params that must validate, params that must not, expected error text)
BLOCK_CASES = [
    (
        "captions",
        {"from": "clip_channel", "style": "tiktok", "case": "upper", "max_words": 3},
        {"from": "clip_channel", "style": "no-such-style"},
        "unknown captions style",
    ),
    (
        "compose",
        {"layers": [{"image": "bg.jpg"}, {"video": "person.mp4", "fit": "contain"}]},
        {"layers": [{"image": "bg.jpg", "fit": "squish"}]},
        ".fit must be one of",
    ),
    (
        "concat",
        {"from": "clip_channel", "transitions_at": "boundaries"},
        {"from": "clip_channel", "transitions_at": "sometimes"},
        "concat.transitions_at",
    ),
    (
        "detect_clips",
        {"method": "silence", "silence_db": -30, "emit": "other"},
        {"method": "engagement", "emit": "other"},
        "is reserved in v1",
    ),
    (
        "export",
        {"from": "clip_channel", "fit": "contain", "canvas": "1080x1920"},
        {"from": "clip_channel", "fit": "squish"},
        "unknown export fit",
    ),
    (
        "filter",
        {"from": "clip_channel", "look": "bw", "grain": 12},
        {"from": "clip_channel", "look": "sepia"},
        "unknown filter look",
    ),
    (
        "music",
        {"from": "clip_channel", "source": "./track.mp3", "fade_out": "2s"},
        {"from": "clip_channel"},
        "music requires a 'source'",
    ),
    (
        "overlay",
        {"from": "clip_channel", "text": "hi", "position": "top-left"},
        {"from": "clip_channel", "text": "hi", "position": "north"},
        "unknown overlay.position",
    ),
    (
        "sfx",
        {"from": "clip_channel", "source": "./whoosh.mp3", "at": [1, "0:02"], "gain": -6},
        {"from": "clip_channel", "at": 1},
        "sfx.source",
    ),
    (
        "still",
        {"image": "./a.jpg", "motion": "zoomin", "motion_amount": 1.2, "emit": "other"},
        {"image": "./a.jpg", "motion": "spin", "emit": "other"},
        "unknown still motion",
    ),
    (
        "zoom",
        {"from": "clip_channel", "amount": 1.3, "duration": "0.2s"},
        {"from": "clip_channel", "amount": 0.5},
        "zoom.amount",
    ),
]


@pytest.mark.parametrize("block,good,_bad,_msg", BLOCK_CASES, ids=[c[0] for c in BLOCK_CASES])
def test_block_valid_params_pass(block, good, _bad, _msg):
    assert validate_doc(_with_step(block, good)) == []


@pytest.mark.parametrize("block,_good,bad,msg", BLOCK_CASES, ids=[c[0] for c in BLOCK_CASES])
def test_block_bad_params_rejected(block, _good, bad, msg):
    errors = validate_doc(_with_step(block, bad))
    assert any(msg in e for e in errors), errors


def test_every_dispatched_block_is_a_real_block():
    """A key that isn't a block name would never be reached."""
    from lemontage import spec
    from lemontage.validator import _BLOCK_CHECKS

    assert set(_BLOCK_CHECKS) <= spec.BUILTIN_BLOCKS


def test_every_block_case_is_dispatched():
    """The table above must cover every block that has its own checks."""
    from lemontage.validator import _BLOCK_CHECKS

    assert {case[0] for case in BLOCK_CASES} == set(_BLOCK_CHECKS)


# -- captions params (no coverage before the split) ----------------------------


@pytest.mark.parametrize(
    "params,msg",
    [
        ({"uppercase": "yes"}, "captions.uppercase must be a boolean"),
        ({"case": "title"}, "captions.case must be 'upper' or 'lower'"),
        ({"pop": 300}, "captions.pop must be a boolean or a scale percent"),
        ({"pop": "loud"}, "captions.pop must be a boolean or a scale percent"),
        ({"pop_duration": "0s"}, "captions.pop_duration must be > 0"),
        ({"pop_duration": "soon"}, "captions.pop_duration must be a duration"),
        ({"pop_on": "letter"}, "captions.pop_on must be 'word' or 'line'"),
        ({"max_words": 0}, "captions.max_words must be an integer >= 1"),
        ({"max_words": True}, "captions.max_words must be an integer >= 1"),
        ({"outline": -1}, "captions.outline must be a number of pixels >= 0"),
        ({"outline": True}, "captions.outline must be a number of pixels >= 0"),
    ],
)
def test_captions_bad_params_rejected(params, msg):
    errors = validate_doc(_with_step("captions", {"from": "clip_channel", **params}))
    assert any(msg in e for e in errors), errors


@pytest.mark.parametrize(
    "params",
    [
        {"uppercase": False},
        {"case": "lower"},
        {"pop": True},
        {"pop": 140},
        {"pop_duration": "0.06s"},
        {"pop_on": "line"},
        {"max_words": 1},
        {"outline": 0},
        {"outline": 3.5},
    ],
)
def test_captions_good_params_pass(params):
    assert validate_doc(_with_step("captions", {"from": "clip_channel", **params})) == []


def test_cloud_model_still_rejected_per_block():
    """The shared provider check must run for every block, not just stt."""
    errors = validate_doc(_with_step("captions", {"from": "clip_channel", "model": "openai"}))
    assert any("reserved for a later phase" in e for e in errors)
