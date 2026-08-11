"""``still`` — render a static image into a short video clip (SPEC §6.11).

Maps over a ``stills`` channel: each image becomes a ``duration``-second clip so
the existing ``export`` (format/fit/title) and ``concat`` (transitions) blocks
can treat it like any other clip. The clip is **video-only** — no audio track is
synthesised — so a downstream ``concat`` must tolerate silent clips.

An optional ``motion`` effect animates the image while it is on screen, driven
by FFmpeg's ``zoompan``: ``zoomout`` starts slightly punched-in and pulls back
to the full frame; ``zoomin`` is the reverse, pushing from the full frame into
the punch-in. Both leave fast and decelerate into the landing (a cubic ease-out
— the "smooth zoom" a CapCut edit gets from an Ease Out velocity curve, not a
constant slide). Both travel in a straight line to (or
from) the picture's subject rather than the middle of the frame
(:func:`smartcrop.focal_point` — the same face detector the podcast reframe
uses). ``panup`` / ``pandown`` are a pure scroll: a full-width,
native-resolution band slides vertically across the image at constant speed —
no zoom involved.
"""

from __future__ import annotations

from typing import Any

from ...spec import STILL_MOTIONS
from .. import ffmpeg, smartcrop
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult

_DEFAULT_DURATION = 3.0
_DEFAULT_FPS = 30
_DEFAULT_MOTION_AMOUNT = 1.1
# Deceleration of the zooms: the eased progress is 1-(1-p)^_EASE, so the move
# spends its speed early and coasts into the landing. 3 (cubic) is the CapCut
# "smooth zoom" feel; 2 is gentler, 4-5 brakes harder. No overshoot at any value.
_EASE = 3


class StillBlock(Block):
    name = "still"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        image = params.get("image") or params.get("input")
        if not image:
            raise ValueError("still: no image (map a 'stills' channel, or set 'image')")
        duration = parse_seconds(params.get("duration", _DEFAULT_DURATION))
        out = ctx.work_dir() / f"{step_id}.mp4"
        _render_still(
            str(image),
            duration,
            out,
            int(params.get("fps", _DEFAULT_FPS)),
            _resolve_motion(params),
        )
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        image = item.get("image")
        if not image:
            raise ValueError("still: channel item has no 'image' (run 'stills' first)")
        duration = parse_seconds(item.get("duration", params.get("duration", _DEFAULT_DURATION)))
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _render_still(
            str(image),
            duration,
            out,
            int(params.get("fps", _DEFAULT_FPS)),
            _resolve_motion(params),
        )
        return ItemResult(item={"clip": str(out)}, outputs={"clips": str(out)})


def _resolve_motion(params: dict[str, Any]) -> tuple[str, float, float | None] | None:
    """Return (motion name, starting zoom, motion length in s), or None for a static clip.

    A None motion length means the pull-back spans the whole clip.
    """
    motion = params.get("motion")
    if motion is None:
        return None
    if not isinstance(motion, str) or motion not in STILL_MOTIONS:
        valid = ", ".join(sorted(STILL_MOTIONS))
        raise ValueError(f"still: unknown motion '{motion}' (choose from: {valid})")
    amount = params.get("motion_amount", _DEFAULT_MOTION_AMOUNT)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 1.0:
        raise ValueError("still: 'motion_amount' must be a number > 1.0")
    raw_dur = params.get("motion_duration")
    motion_dur = None
    if raw_dur is not None:
        motion_dur = parse_seconds(raw_dur)
        if motion_dur <= 0:
            raise ValueError("still: 'motion_duration' must be > 0")
    return motion, float(amount), motion_dur


def _render_still(
    image: str,
    duration: float,
    out,
    fps: int,
    motion: tuple[str, float, float | None] | None = None,
) -> None:
    # Loop a single image for `duration` seconds into an H.264 clip. yuv420p keeps
    # it broadly playable; the scale rounds to even dimensions (libx264 requires
    # even width/height). No audio track is added.
    if motion is not None:
        name = motion[0]
        if name in ("panup", "pandown"):
            _render_pan(image, duration, out, fps, *motion)
        else:
            _render_zoom(image, duration, out, fps, *motion)
        return
    ffmpeg.run(
        [
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image),
            "-r",
            str(fps),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )


def _render_pan(
    image: str,
    duration: float,
    out,
    fps: int,
    name: str,
    amount: float,
    motion_dur: float | None,
) -> None:
    # Slide a native-resolution horizontal band down (panup: the picture moves
    # up) or up the image at constant speed over `motion_dur` seconds (the whole
    # clip if unset), then hold. A moving `crop` — no zoom, no rescale: `amount`
    # only sets the band height (ih/amount), i.e. how far the view can travel.
    dur = motion_dur if motion_dur is not None else duration
    progress = f"min(t/{dur:.3f},1)"
    travel = f"({progress})" if name == "panup" else f"(1-{progress})"
    ffmpeg.run(
        [
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image),
            "-r",
            str(fps),
            "-vf",
            f"crop=w=iw:h=ih/{amount}:x=0:y='{travel}*(ih-oh)',scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )


def _render_zoom(
    image: str,
    duration: float,
    out,
    fps: int,
    name: str,
    amount: float,
    motion_dur: float | None,
) -> None:
    # zoompan eases the zoom between 1.0 and `amount` over `motion_dur` seconds
    # (the whole clip if unset), then holds the landing frame; the cubic ease-out
    # leaves fast and decelerates into the landing, both ways — that braking is
    # what reads as a "smooth zoom" rather than a slider. The 2x pre-upscale hides
    # zoompan's integer-pan jitter on small zooms; `s=` brings the frame back to
    # the image's own (even) size afterwards.
    width, height = ffmpeg.probe_resolution(image)
    ow, oh = width - width % 2, height - height % 2
    frames = max(round(duration * fps), 2)
    span = frames - 1
    if motion_dur is not None:
        span = min(max(round(motion_dur * fps), 1), span)
    progress = f"min(on/{span},1)"
    # Aim the punch-in at the subject: the window centre travels in a straight
    # line between the frame centre (full frame, zoom 1.0) and the focal point
    # (fully punched in, zoom `amount`), indexed on the zoom's own eased
    # progress `(zoom-1)/(amount-1)` — so the move goes *straight* at the face
    # and lands with the zoom, instead of wandering while the clamp releases it.
    # Pinning the centre on the focal point at every zoom level was the detour:
    # at zoom 1.0 the window is the whole image and can only sit at 0, so the
    # centre swung sideways as the clamp let go. Same expression both ways —
    # `zoomout` runs the zoom amount -> 1.0, so it starts on the face and pulls
    # back to the full frame. A centred focal point => the old centred move.
    fx, fy = smartcrop.focal_point(image) or (0.5, 0.5)
    ease = f"min(1,(zoom-1)/({amount}-1))"
    x = f"max(0,min(iw-iw/zoom,(0.5+({fx:.4f}-0.5)*{ease})*iw-(iw/zoom/2)))"
    y = f"max(0,min(ih-ih/zoom,(0.5+({fy:.4f}-0.5)*{ease})*ih-(ih/zoom/2)))"
    if name == "zoomout":
        zoom = f"1+({amount}-1)*pow(1-{progress},{_EASE})"
    else:
        zoom = f"{amount}-({amount}-1)*pow(1-{progress},{_EASE})"
    ffmpeg.run(
        [
            "-i",
            str(image),
            "-vf",
            f"scale=iw*2:ih*2,zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={ow}x{oh}:fps={fps}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
