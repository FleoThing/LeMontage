"""Subject-following vertical crop for `export` (SPEC §6.6, ``smart_crop``).

Filling a 9:16 frame from a landscape source normally either bars it (`contain`)
or centre-crops it (`cover`) — both lose the subject when it isn't centred. Smart
crop instead slides a tall crop window left/right to keep the subject in frame:

1. sample the source a few times a second and find the main face's horizontal
   centre with mediapipe (falls back to holding the last position, then centre);
2. smooth the trajectory so the frame drifts rather than jumps;
3. emit an FFmpeg ``scale`` → ``sendcmd`` → ``crop`` chain that moves the crop's
   ``x`` over time (``crop``'s ``x`` is a runtime command).

mediapipe + OpenCV are behind the optional ``[smartcrop]`` extra.

ponytail: sendcmd sets a stepwise x at each sample (no interpolation between
commands); dense sampling + EMA keeps it smooth enough. Upgrade path: a
per-frame ``x`` expression (lerp between keyframes) if the steps ever show.
"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg

_SAMPLE_FPS = 4.0  # subject positions sampled per second of source
_EMA_ALPHA = 0.25  # trajectory smoothing (lower = smoother / laggier)


def crop_filters(media: str, target_w: int, target_h: int, cmd_path: Path) -> list[str]:
    """Return the FFmpeg video filters that crop ``media`` to
    ``target_w``×``target_h`` following the subject. Writes the sendcmd script to
    ``cmd_path``. Falls back to a static centre crop when the source is not wider
    than the target (nothing to pan) or no subject is ever found."""
    src_w, src_h = ffmpeg.probe_resolution(media)
    # Scale so the source height fills the frame; its width then overflows and is
    # what we pan across.
    scaled_w = _even(round(src_w * target_h / src_h))
    if scaled_w <= target_w:
        # Source no wider than the target once height-matched: nothing to pan.
        return [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
            f"crop={target_w}:{target_h}",
        ]

    max_x = scaled_w - target_w
    track = _track_subject(media)
    xs = [(t, _clamp(round(cx * scaled_w - target_w / 2), 0, max_x)) for t, cx in track]
    if not xs:
        xs = [(0.0, max_x // 2)]  # no subject ever found → centre

    lines = [f"{t:.3f} crop x {x};" for t, x in xs]
    cmd_path.write_text("\n".join(lines), encoding="utf-8")
    return [
        f"scale={scaled_w}:{target_h}",
        f"sendcmd=f={_escape(cmd_path)}",
        f"crop={target_w}:{target_h}:{xs[0][1]}:0",
    ]


def _track_subject(media: str) -> list[tuple[float, float]]:
    """Sample the source and return [(time, subject_centre_x in 0..1)].

    Uses mediapipe face detection; when a frame has no face the previous centre
    is held (then 0.5 before the first hit). The result is EMA-smoothed.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ValueError(
            "export: smart_crop needs mediapipe + OpenCV — install with "
            "pip install 'lemontage[smartcrop]'"
        ) from exc

    duration = ffmpeg.probe_duration(media)
    cap = cv2.VideoCapture(media)
    detector = mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.5
    )
    raw: list[tuple[float, float | None]] = []
    try:
        n = max(1, int(duration * _SAMPLE_FPS))
        for i in range(n):
            t = i / _SAMPLE_FPS
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            result = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            raw.append((t, _largest_face_cx(result)))
    finally:
        cap.release()
        detector.close()
    return _smooth(raw)


def _largest_face_cx(result) -> float | None:
    """Horizontal centre (0..1) of the largest detected face, or None."""
    detections = getattr(result, "detections", None)
    if not detections:
        return None
    best = max(detections, key=lambda d: d.location_data.relative_bounding_box.width)
    box = best.location_data.relative_bounding_box
    return _clamp(box.xmin + box.width / 2, 0.0, 1.0)


def _smooth(raw: list[tuple[float, float | None]]) -> list[tuple[float, float]]:
    """Fill gaps (hold last / centre) then exponentially smooth the centre-x."""
    out: list[tuple[float, float]] = []
    ema = 0.5  # centre before the first detection
    for t, cx in raw:
        if cx is not None:
            ema = _EMA_ALPHA * cx + (1 - _EMA_ALPHA) * ema
        out.append((t, ema))
    return out


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _escape(path: Path) -> str:
    """Escape a path for use as an FFmpeg filter option value."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
