"""``sfx`` — drop sound effects at chosen moments (SPEC §6.16).

`music` lays one continuous track over a finished reel. A whoosh on a cut, a
ding on the punchline, a riser under a reveal are the opposite: the *same* short
sample, dropped at several exact times, mixed under whatever audio is already
there. That was the one audio move the engine could not make.

Works on the pipeline input or maps over a channel, so effects can land per clip
(`at` is clip-relative) or once over the whole reel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult, ItemResult


class SfxBlock(Block):
    name = "sfx"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if media is None:
            raise ValueError("sfx: no input media")
        out = ctx.work_dir() / f"{step_id}.mp4"
        _mix(str(media), params, out)
        return BlockResult(outputs={"clip": str(out)})

    def execute_item(
        self, params: dict[str, Any], item: dict[str, Any], ctx: RunContext, step_id: str
    ) -> ItemResult:
        # Same rule as `captions`: the exported file once there is one.
        key = "file" if item.get("file") else "clip"
        clip = item.get(key)
        if clip is None:
            raise ValueError("sfx: channel item has no 'clip' (run 'cut' first)")
        out = ctx.work_dir() / f"{step_id}-{item['index']}.mp4"
        _mix(str(clip), params, out)
        return ItemResult(item={key: str(out)}, outputs={"clips": str(out)})


def times_of(params: dict[str, Any]) -> list[float]:
    at = params.get("at", [0])
    if not isinstance(at, list):
        at = [at]
    times = sorted(parse_seconds(value) for value in at)
    if any(t < 0 for t in times):
        raise ValueError("sfx: 'at' times must be >= 0")
    return times


def mix_filter(times: list[float], gain: float, has_audio: bool) -> str:
    """The filter graph: one delayed copy of the sample per hit, then a mix.

    The sample is split rather than opened N times so a hit costs a filter, not
    a decode. `normalize=0` keeps the original audio at its own level — amix's
    default would duck the voice by 1/N every time an effect fires, which is
    exactly the artefact this block exists to avoid.
    """
    copies = len(times)
    parts = []
    labels = [f"s{i}" for i in range(copies)]
    if copies > 1:
        parts.append(f"[1:a]asplit={copies}" + "".join(f"[{label}]" for label in labels))
    else:
        labels = ["1:a"]
    hits = []
    for i, (label, at) in enumerate(zip(labels, times, strict=True)):
        chain = [f"volume={gain:.2f}dB"] if gain else []
        if at > 0:
            chain.append(f"adelay={int(round(at * 1000))}:all=1")
        hit = f"h{i}"
        parts.append(f"[{label}]{','.join(chain) or 'anull'}[{hit}]")
        hits.append(f"[{hit}]")
    inputs = (["[0:a]"] if has_audio else []) + hits
    if len(inputs) == 1:
        parts.append(f"{inputs[0]}anull[a]")
    else:
        parts.append(f"{''.join(inputs)}amix=inputs={len(inputs)}:duration=first:normalize=0[a]")
    return ";".join(parts)


def _mix(media: str, params: dict[str, Any], out: Path) -> None:
    source = params.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("sfx: 'source' (path to an audio file) is required")
    if not Path(source).exists():
        raise ValueError(f"sfx: source not found: {source}")
    gain = float(params.get("gain", 0))
    graph = mix_filter(times_of(params), gain, ffmpeg.has_audio(media))
    ffmpeg.run(
        [
            "-i",
            str(media),
            "-i",
            str(source),
            "-filter_complex",
            graph,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            # The effects must never extend the clip: a hit near the end is cut
            # with it, exactly as it would be in an editor's timeline.
            "-shortest",
            str(out),
        ]
    )
