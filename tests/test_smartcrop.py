"""Tests for subject-following `export: smart_crop` (mediapipe/OpenCV mocked)."""

import sys
import types

import pytest

from lemontage.engine import ffmpeg, smartcrop
from lemontage.engine.blocks.export import ExportBlock
from lemontage.engine.context import RunContext
from lemontage.validator import validate_doc


def ctx(tmp_path):
    return RunContext(
        vars={}, input={"source": "ep.mp4"}, matrix={}, output_dir=tmp_path, pipeline_name="demo"
    )


class _Box:
    def __init__(self, xmin, width):
        self.xmin, self.width = xmin, width


class _Detection:
    def __init__(self, xmin, width):
        self.location_data = type("L", (), {"relative_bounding_box": _Box(xmin, width)})()


class _Result:
    def __init__(self, *boxes):
        self.detections = [_Detection(x, w) for x, w in boxes]


def test_crop_filters_follows_subject_with_a_per_frame_expression(monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1920, 1080))
    # Subject sits left, then settles far right — one deliberate move.
    track = [(t / 6, 0.2) for t in range(12)] + [(2 + t / 6, 0.8) for t in range(12)]
    monkeypatch.setattr(smartcrop, "_track_subject", lambda m: track)

    filters = smartcrop.crop_filters("in.mp4", 1080, 1920)

    assert filters[0] == "scale=3414:1920"  # height-matched, width overflows
    crop = filters[1]
    assert crop.startswith("crop=1080:1920:x='") and crop.endswith("':y=0")
    # A moving x, evaluated per frame — not a stepwise sendcmd script.
    assert "sendcmd" not in crop and "min(1,max(0,(t-" in crop


def test_crop_filters_static_when_source_not_wider(monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1080, 1920))
    called = []
    monkeypatch.setattr(smartcrop, "_track_subject", lambda m: called.append(1) or [])
    filters = smartcrop.crop_filters("in.mp4", 1080, 1920)
    assert "force_original_aspect_ratio=increase" in filters[0]
    assert not called  # no tracking work when there's nothing to pan


def test_crop_filters_centre_crop_when_no_subject_found(monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(smartcrop, "_track_subject", lambda m: [])
    filters = smartcrop.crop_filters("in.mp4", 1080, 1920)
    assert filters == ["scale=3414:1920", "crop=1080:1920:1167:0"]  # static, centred


def test_keyframes_hold_through_small_drift():
    """A head that wobbles inside the deadzone must not move the frame at all."""
    xs = [(i / 6, 500 + (10 if i % 2 else -10)) for i in range(30)]
    keys = smartcrop._keyframes(xs, deadzone=80, snap=300)
    assert len(keys) == 1 and keys[0][:2] == (0.0, 490)


def test_keyframes_glide_past_the_deadzone_and_snap_on_a_cut():
    xs = [(0.0, 100.0), (1.0, 300.0), (5.0, 900.0)]
    keys = smartcrop._keyframes(xs, deadzone=80, snap=300)
    assert [k[0] for k in keys] == [0.0, 1.0, 5.0]
    assert keys[1][2] == smartcrop._RAMP  # a pan: eased glide
    assert keys[2][2] == smartcrop._SNAP_RAMP  # a jump that wide reads as a camera cut


def test_keyframes_drop_moves_that_would_cut_a_glide_short():
    xs = [(0.0, 100.0), (1.0, 300.0), (1.1, 500.0), (9.0, 700.0)]
    keys = smartcrop._keyframes(xs, deadzone=80, snap=1000)
    assert [k[0] for k in keys] == [0.0, 1.0, 9.0]  # 1.1 lands mid-glide → dropped


def test_x_expr_is_a_flat_sum_of_eased_ramps():
    expr = smartcrop._x_expr([(0.0, 100.0, 0.0), (2.0, 300.0, 0.7)])
    assert expr.startswith("100.0+(200.0)*(")
    assert "min(1,max(0,(t-2.000)/0.700))" in expr
    assert "if(" not in expr  # no nesting: one term per move


def test_median_kills_a_single_bad_detection():
    samples = [(0.0, 0.5), (0.1, 0.5), (0.2, 0.95), (0.3, 0.5), (0.4, 0.5)]
    assert [v for _, v in smartcrop._median(samples, 5)] == [0.5] * 5


def test_tracked_face_prefers_the_locked_subject_over_the_largest():
    """A two-shot: the other speaker leaning in must not steal the frame."""
    result = _Result((0.60, 0.30), (0.10, 0.12))  # big face right, small locked face left
    assert smartcrop._tracked_face_cx(result, locked=0.16) == pytest.approx(0.16)
    # Locked subject gone (nothing within the lock radius) → fall back to largest.
    assert smartcrop._tracked_face_cx(result, locked=0.99) == pytest.approx(0.75)
    assert smartcrop._tracked_face_cx(_Result(), locked=0.5) is None


def test_export_smart_crop_builds_subject_chain(tmp_path, monkeypatch):
    args = {}
    monkeypatch.setattr(ffmpeg, "run", lambda a: args.setdefault("a", a))
    monkeypatch.setattr(
        smartcrop,
        "crop_filters",
        lambda m, w, h: ["scale=3414:1920", "crop=1080:1920:x='100':y=0"],
    )
    ExportBlock().execute({"smart_crop": True, "resolution": "1080x1920"}, ctx(tmp_path), "export")
    vf = args["a"][args["a"].index("-vf") + 1]
    assert "crop=1080:1920:x='100':y=0" in vf and vf.endswith("fps=30")


def test_validator_rejects_non_bool_smart_crop():
    doc = {
        "lemontage": "1.0",
        "name": "t",
        "input": {"type": "video", "source": "a.mp4"},
        "steps": [{"export": {"smart_crop": "yes"}}],
    }
    assert any("smart_crop must be a boolean" in e for e in validate_doc(doc))


def test_missing_extra_raises_helpful_error(monkeypatch):
    """Without mediapipe installed, _track_subject points at the extra."""
    monkeypatch.setitem(sys.modules, "mediapipe", None)  # `import mediapipe` → ImportError
    with pytest.raises(ValueError, match="lemontage\\[smartcrop\\]"):
        smartcrop._track_subject("in.mp4")


def test_mediapipe_1x_raises_a_version_error(monkeypatch):
    """mediapipe 1.x dropped `mp.solutions` — say so instead of an AttributeError."""
    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "mediapipe", types.SimpleNamespace(__version__="1.0.0"))
    with pytest.raises(ValueError, match="mediapipe < 1.0"):
        smartcrop._track_subject("in.mp4")
