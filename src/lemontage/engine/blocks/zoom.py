"""``zoom`` — punch in on a video clip (SPEC §6.15).

The move that carries short-form talking-head edits: the frame snaps 10–20%
closer on a punchline, holds, and snaps back. `still` can already do this for
images (`motion: zoomin`), but nothing could do it to *video* — the only zoom on
a clip was whatever the camera did.

Two shapes, one param apart:

* no ``at`` — a **static** punch for the whole clip. Give ``amount`` a list and
  each clip gets its own framing, so every jump cut changes the shot size (the
  cheapest way to make a cut-heavy reel stop looking like one locked-off take).
  Rendered as `crop` + `scale`: exact, and no zoompan jitter.
* with ``at`` — a **punch at each time**, alternating in / out / in, eased over
  ``duration``. Rendered with `zoompan`, whose zoom expression is a flat sum of
  clamped ramps (same shape as the smart-crop trajectory).

Works on the pipeline input or maps over a channel; place it before `export`
(source-shaped, one re-encode later) or after it (punch the finished frame).
"""

from __future__ import annotations

from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult

_DEFAULT_AMOUNT = 1.15  # a punch you feel without losing the framing
_DEFAULT_RAMP = 0.15  # snap, not a slow push


class ZoomBlock(Block):
    name = "zoom"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("zoom: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _apply(str(media), params, 0, out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        # Punch whatever the chain is carrying: the exported file after `export`,
        # the cut clip before it (same rule as `captions`).
        key = "file" if item.get("file") else "clip"
        clip = item.get(key)
        if clip is None:
            raise ValueError("zoom: channel item has no 'clip' (run 'cut' first)")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _apply(str(clip), params, item["index"], out)
        return ItemResult(item={key: str(out)}, outputs={"clips": str(out)})


def amount_for(params: dict[str, Any], index: int) -> float:
    """The zoom factor for this clip: a number, or a list read by position.

    A position past the end of the list means "no punch" (1.0) rather than an
    error, so `amount: [1.0, 1.2]` alternates over any number of clips without
    the pipeline having to know how many there are.
    """
    amount = params.get("amount", _DEFAULT_AMOUNT)
    if isinstance(amount, list):
        amount = amount[index] if index < len(amount) else 1.0
    value = float(amount)
    if value < 1.0:
        raise ValueError(f"zoom: amount must be >= 1.0 (got {value})")
    return value


def punch_expr(amount: float, ats: list[float], ramp: float, fps: float) -> str:
    """The `zoompan` z expression: 1.0, punched at each ``at``, alternating.

    Flat sum of eased ramps — each punch adds its own delta, clamped to 0 before
    it starts and 1 after it lands::

        1 + (A-1)*ease(t-at1) - (A-1)*ease(t-at2) + …

    `zoompan` counts output frames (`on`), so time is ``on/fps``.
    """
    terms = []
    for i, at in enumerate(ats):
        delta = (amount - 1.0) if i % 2 == 0 else (1.0 - amount)
        s = f"min(1,max(0,(on/{fps:.3f}-{at:.3f})/{ramp:.3f}))"
        terms.append(f"+({delta:.4f})*({s}*{s}*(3-2*{s}))")
    return "1" + "".join(terms)


def _ats(params: dict[str, Any]) -> list[float]:
    at = params.get("at")
    if at is None:
        return []
    if not isinstance(at, list):
        raise ValueError("zoom: 'at' must be a list of times")
    return sorted(parse_seconds(value) for value in at)


def _apply(media: str, params: dict[str, Any], index: int, out) -> None:
    amount = amount_for(params, index)
    ats = _ats(params)
    width, height = ffmpeg.probe_resolution(media)
    if not ats:
        if amount == 1.0:  # nothing to do for this clip — keep it as it is
            vf = f"scale={width}:{height}"
        else:
            vf = f"crop=trunc(iw/{amount}/2)*2:trunc(ih/{amount}/2)*2,scale={width}:{height}"
    else:
        ramp = parse_seconds(params.get("duration", _DEFAULT_RAMP))
        if ramp <= 0:
            raise ValueError("zoom: 'duration' must be > 0")
        # zoompan re-times to its `fps`, so it must be given the source's own —
        # anything else silently speeds the clip up or slows it down.
        fps = ffmpeg.probe_fps(media) or 30.0
        expr = punch_expr(amount, ats, ramp, fps)
        # ponytail: zoompan quantises the pan to whole pixels, which can shimmer
        # during a long, slow push. Fine for a snap; pre-upscale like `still`
        # does if slow pushes ever look bad.
        vf = (
            f"zoompan=z='{expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={width}x{height}:fps={fps}"
        )
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "copy",
            str(out),
        ]
    )
