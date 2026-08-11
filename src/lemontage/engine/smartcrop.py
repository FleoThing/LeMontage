"""Subject-following vertical crop for `export` (SPEC §6.6, ``smart_crop``).

Filling a 9:16 frame from a landscape source normally either bars it (`contain`)
or centre-crops it (`cover`) — both lose the subject when it isn't centred. Smart
crop instead slides a tall crop window left/right to keep the subject in frame.

The framing has to be **still**: a window that follows every head movement is
worse than a static crop. So the trajectory is built like a camera operator
works — hold, then move only when the subject really left the middle:

1. sample the source a few times a second and find the tracked face's horizontal
   centre (frames are read sequentially and downscaled — no per-sample seeking);
2. **lock onto one subject**: among several faces, keep the one nearest the
   previous position instead of the largest, so a two-shot doesn't make the frame
   ping-pong between speakers;
3. **median-filter** the samples (a single bad detection cannot move the frame);
4. **hold inside a deadzone**: the window only moves once the subject drifts
   further than ``_DEADZONE`` of the crop width from where it sits, which turns a
   continuous trajectory into a few deliberate moves;
5. **glide, don't step**: the moves become an eased per-frame ``crop`` x
   expression (a sum of clamped ramps), so the frame slides over ``_RAMP``
   seconds. A jump wider than ``_SNAP_JUMP`` is treated as a camera cut and
   snapped in ``_SNAP_RAMP``.

The same detector also answers a smaller question for ``still``:
:func:`focal_point` — where a zoom on a photo should aim (see §6.11).

Detection is YuNet, run through OpenCV's own :class:`cv2.FaceDetectorYN` — no
separate inference dependency, and it sees the painted and engraved faces an
archive edit is mostly made of. OpenCV is behind the optional ``[smartcrop]``
extra.
"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg

# YuNet, vendored from OpenCV Zoo (MIT, Shiqi Yu et al.) — 227 KB, so it ships
# rather than being downloaded on first run.
_YUNET_MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
_YUNET_CONFIDENCE = 0.6  # painted portraits score 0.87-0.93; below this it starts inventing faces

_SAMPLE_FPS = 6.0  # subject positions sampled per second of source
_DETECT_WIDTH = 640  # frames are downscaled to this before face detection
_MEDIAN_WINDOW = 5  # samples (~0.8s) — one bad detection can't move the frame
_DEADZONE = 0.08  # drift (as a fraction of the crop width) tolerated before moving
_RAMP = 0.7  # seconds to glide to the new position
_SNAP_JUMP = 0.30  # |Δx| / crop width above which the move is a cut, not a pan
_SNAP_RAMP = 0.12  # glide time for those (a snap)
_LOCK_RADIUS = 0.22  # a face this close (0..1 of frame width) to the last one is "the same"
_MAX_KEYFRAMES = 240  # ponytail: expression length ceiling; widen the deadzone past it
_GRID = 8  # focal_point: cells per side when looking for the busiest part of a photo


def crop_filters(media: str, target_w: int, target_h: int) -> list[str]:
    """Return the FFmpeg video filters that crop ``media`` to
    ``target_w``×``target_h`` following the subject. Falls back to a static centre
    crop when the source is not wider than the target (nothing to pan) or no
    subject is ever found."""
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
    xs = [(t, float(_clamp(cx * scaled_w - target_w / 2, 0, max_x))) for t, cx in track]
    if not xs:  # no subject ever found → a plain centre crop
        return [f"scale={scaled_w}:{target_h}", f"crop={target_w}:{target_h}:{max_x // 2}:0"]

    keys = _keyframes(xs, deadzone=_DEADZONE * target_w, snap=_SNAP_JUMP * target_w)
    return [
        f"scale={scaled_w}:{target_h}",
        f"crop={target_w}:{target_h}:x='{_x_expr(keys)}':y=0",
    ]


def focal_point(image: str) -> tuple[float, float] | None:
    """Where a ``still`` zoom should aim, as (x, y) in 0..1 — or None to stay centred.

    Zooming into the middle of the frame is the slideshow tic: on a portrait the
    move drifts off the face, on a wide scene it lands on nothing. Same idea as
    the podcast reframe above, minus the tracking — one image, one point:

    1. the largest face, when there is one (a portrait, a group). Painted and
       engraved faces count: YuNet found all six of a set of Napoleon portraits at
       ≥0.87, including a head shadowed under a bicorne and one a few dozen pixels
       wide on a rearing horse;
    2. otherwise the busiest cell of the picture — the coarse edge-energy grid,
       which on a battle scene or a landscape lands on the figures rather than
       the sky. It is a poor stand-in for a face, and visibly so: on those same
       portraits it aimed at the torso (y≈0.31-0.56) where the heads were at
       y≈0.13-0.29 — the "zoom lands on the uniform, not the head" failure;
    3. None when neither can be computed (no OpenCV, unreadable file), and the
       caller keeps the plain centred move.
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover - exercised only without the extra
        return None
    frame = cv2.imread(str(image))
    if frame is None:  # not an image, or unreadable
        return None
    small = _downscale(cv2, frame)
    return _largest_face(cv2, small) or _busiest_cell(cv2, small)


def _detector(cv2, width: int, height: int):
    """A YuNet detector sized for ``width``×``height`` frames, or None if unusable.

    The input size is baked in at creation, which is why callers detect on frames
    already passed through :func:`_downscale` — one detector then serves a whole
    video instead of one per frame.
    """
    if not (hasattr(cv2, "FaceDetectorYN") and _YUNET_MODEL.exists()):
        return None
    # OpenCV 5 prints a "Targets are not supported by the new graph engine" warning
    # per detector, straight to stderr, in the middle of the run report. Nothing is
    # wrong and there is no flag for it, so the log level is lowered around the
    # call and put back — a global, but a briefly held one.
    logging = getattr(getattr(cv2, "utils", None), "logging", None)
    previous = logging.getLogLevel() if logging else None
    if logging:
        logging.setLogLevel(logging.LOG_LEVEL_ERROR)
    try:
        return cv2.FaceDetectorYN.create(str(_YUNET_MODEL), "", (width, height), _YUNET_CONFIDENCE)
    finally:
        if logging:
            logging.setLogLevel(previous)


def _detect_faces(cv2, frame, detector=None) -> list[tuple[float, float, float]]:
    """Faces in ``frame`` as [(centre_x, centre_y, width)], all normalised 0..1."""
    height, width = frame.shape[:2]
    detector = detector or _detector(cv2, width, height)
    if detector is None:
        return []
    _, faces = detector.detect(frame)
    if faces is None or not len(faces):
        return []
    # YuNet rows are [x, y, w, h, 5 landmarks…, score], in pixels and as numpy
    # floats — cast, so callers and the filter expressions see plain floats.
    return [
        (
            float(_clamp((f[0] + f[2] / 2) / width, 0.0, 1.0)),
            float(_clamp((f[1] + f[3] / 2) / height, 0.0, 1.0)),
            float(f[2]) / width,
        )
        for f in faces
    ]


def _largest_face(cv2, frame) -> tuple[float, float] | None:
    """Centre (x, y in 0..1) of the biggest face in a single frame, or None.

    Largest, not nearest-to-last: a photo has no previous frame to lock onto.
    """
    faces = _detect_faces(cv2, frame)
    if not faces:
        return None
    cx, cy, _ = max(faces, key=lambda f: f[2])
    return cx, cy


def _busiest_cell(cv2, frame) -> tuple[float, float] | None:
    """Centre of the highest-detail cell of a coarse grid over the frame.

    The energy *centroid* would be useless here — averaged over a whole painting
    it sits back in the middle. Averaging per cell and taking the brightest one
    picks a region instead: where the detail is.
    """
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    energy = cv2.convertScaleAbs(cv2.Laplacian(cv2.GaussianBlur(grey, (5, 5), 0), cv2.CV_32F))
    cells = cv2.resize(energy, (_GRID, _GRID), interpolation=cv2.INTER_AREA)
    flat = cells.reshape(-1)
    best = int(flat.argmax())
    if not flat[best]:  # a flat colour field — nothing to aim at
        return None
    return (best % _GRID + 0.5) / _GRID, (best // _GRID + 0.5) / _GRID


def _track_subject(media: str) -> list[tuple[float, float]]:
    """Sample the source and return [(time, subject_centre_x in 0..1)].

    Frames are read sequentially (seeking per sample is slow and imprecise) and
    downscaled before detection. Samples with no face are dropped — the caller
    holds the last known position. The result is median-filtered.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ValueError(
            "export: smart_crop needs OpenCV — install with pip install 'lemontage[smartcrop]'"
        ) from exc
    if not hasattr(cv2, "FaceDetectorYN"):
        raise ValueError(
            f"export: smart_crop needs OpenCV >= 4.5.4 for its face detector "
            f"(installed: {getattr(cv2, '__version__', 'unknown')}) — "
            "pip install --upgrade 'lemontage[smartcrop]'"
        )

    cap = cv2.VideoCapture(media)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    step = max(1, round(src_fps / _SAMPLE_FPS)) if src_fps > 0 else 1
    detector = None  # built on the first frame, once its downscaled size is known
    raw: list[tuple[float, float]] = []
    locked: float | None = None
    try:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                small = _downscale(cv2, frame)
                if detector is None:
                    height, width = small.shape[:2]
                    detector = _detector(cv2, width, height)
                cx = _tracked_face_cx(_detect_faces(cv2, small, detector), locked)
                if cx is not None:
                    locked = cx
                    raw.append((index / src_fps if src_fps > 0 else 0.0, cx))
            index += 1
    finally:
        cap.release()
    return _median(raw, _MEDIAN_WINDOW)


def _downscale(cv2, frame):
    """Shrink a frame to ``_DETECT_WIDTH`` — detection doesn't need full res."""
    height, width = frame.shape[:2]
    if width <= _DETECT_WIDTH:
        return frame
    scale = _DETECT_WIDTH / width
    return cv2.resize(frame, (_DETECT_WIDTH, max(1, round(height * scale))))


def _tracked_face_cx(faces, locked: float | None) -> float | None:
    """Horizontal centre (0..1) of the *tracked* face, or None when none is found.

    Takes the ``[(cx, cy, width)]`` list :func:`_detect_faces` returns. With
    several faces (a two-shot, an interviewer in frame) the largest one flips as
    people lean in and out, and the frame ping-pongs. So once a subject is locked,
    prefer the detection nearest to it; only fall back to the largest when the
    locked subject is gone (nothing within ``_LOCK_RADIUS``).
    """
    if not faces:
        return None
    if locked is not None:
        near = [f for f in faces if abs(f[0] - locked) <= _LOCK_RADIUS]
        if near:
            return min(near, key=lambda f: abs(f[0] - locked))[0]
    return max(faces, key=lambda f: f[2])[0]


def _median(samples: list[tuple[float, float]], window: int) -> list[tuple[float, float]]:
    """Median-filter the centre-x over a sliding window of samples."""
    if window <= 1 or len(samples) < window:
        return samples
    half = window // 2
    out: list[tuple[float, float]] = []
    for i, (t, _) in enumerate(samples):
        chunk = sorted(v for _, v in samples[max(0, i - half) : i + half + 1])
        out.append((t, chunk[len(chunk) // 2]))
    return out


def _keyframes(
    xs: list[tuple[float, float]], deadzone: float, snap: float
) -> list[tuple[float, float, float]]:
    """Turn a per-sample trajectory into ``[(time, x, ramp)]`` moves.

    The window holds its position until the subject drifts past ``deadzone``, then
    a move is emitted: an eased glide of ``_RAMP`` seconds, or a ``_SNAP_RAMP``
    snap when the jump is wider than ``snap`` (a camera cut, not a head turn).
    Moves closer together than their ramp are dropped so each glide completes —
    that is also what keeps the expression short.
    """
    held = xs[0][1]
    keys: list[tuple[float, float, float]] = [(0.0, held, 0.0)]
    for t, x in xs[1:]:
        if abs(x - held) <= deadzone:
            continue
        ramp = _SNAP_RAMP if abs(x - held) > snap else _RAMP
        last_t, _, last_ramp = keys[-1]
        if t < last_t + last_ramp:  # previous glide still running — let it land
            continue
        keys.append((t, x, ramp))
        held = x
        if len(keys) >= _MAX_KEYFRAMES:
            break
    return keys


def _x_expr(keys: list[tuple[float, float, float]]) -> str:
    """Build the per-frame ``crop`` x expression for a list of moves.

    Flat sum of eased ramps rather than nested ``if()``s: each move adds its own
    delta, clamped to 0 before it starts and 1 after it lands, with a smoothstep
    so the frame accelerates and settles instead of sliding linearly::

        x0 + Δ1*ease(clip((t-t1)/r1)) + Δ2*ease(clip((t-t2)/r2)) + …
    """
    start = keys[0][1]
    terms = []
    for i in range(1, len(keys)):
        t, x, ramp = keys[i]
        delta = x - keys[i - 1][1]
        s = f"min(1,max(0,(t-{t:.3f})/{ramp:.3f}))"
        terms.append(f"+({delta:.1f})*({s}*{s}*(3-2*{s}))")
    return f"{start:.1f}" + "".join(terms)


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))
