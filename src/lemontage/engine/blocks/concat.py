"""``concat`` — stitch a channel's clips into a single video (SPEC §6.7).

A channel *aggregator* (``maps = False``): instead of running once per item, it
receives the whole channel and joins the clips in order into one file. Place it
after ``export`` to assemble the rendered clips into a final reel.

Plain concatenation uses FFmpeg's concat demuxer (fast, no re-encode of the
joins). Passing ``transitions`` switches to a ``xfade``/``acrossfade``
filter-graph chain instead, crossfading video and audio across each gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg
from ...spec import CONCAT_TRANSITIONS
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult

_DEFAULT_TRANSITION_DURATION = 0.5


class ConcatBlock(Block):
    name = "concat"
    maps = False

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        raise ValueError("concat: requires 'from: <channel>'")

    def execute_channel(
        self, params: dict[str, Any], items: list[dict[str, Any]], ctx: RunContext, step_id: str
    ) -> BlockResult:
        ordered = sorted(items, key=lambda it: it.get("index", 0))
        # Prefer the exported file; fall back to the cut/captioned clip.
        files = [it.get("file") or it.get("clip") for it in ordered]
        files = [f for f in files if f]
        if not files:
            return BlockResult(outputs={})
        out = _output_path(params, ctx)
        transitions = _resolve_transitions(params, len(files))
        if transitions is None:
            _concat(files, out, ctx.work_dir() / f"{step_id}-list.txt")
        else:
            duration = parse_seconds(params.get("duration", _DEFAULT_TRANSITION_DURATION))
            _concat_with_transitions(files, transitions, duration, out)
        return BlockResult(outputs={"file": str(out), "parts": files})


def _output_path(params: dict[str, Any], ctx: RunContext) -> Path:
    template = params.get("output")
    if template:
        rendered = (
            str(template)
            .replace("{{ name }}", ctx.pipeline_name)
            .replace("{{name}}", ctx.pipeline_name)
        )
        out = Path(rendered)
    else:
        out = ctx.output_dir / f"{ctx.pipeline_name}-reel.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _resolve_transitions(params: dict[str, Any], n_files: int) -> list[str] | None:
    """Return one transition name per gap between clips, or None for a plain concat."""
    raw = params.get("transitions")
    if raw is None:
        return None
    n_gaps = n_files - 1
    if n_gaps <= 0:
        raise ValueError("concat: 'transitions' given but there is only 1 clip to join")

    if isinstance(raw, str):
        names = [raw] * n_gaps
    elif isinstance(raw, list):
        if len(raw) != n_gaps:
            raise ValueError(
                f"concat: 'transitions' has {len(raw)} entrie(s) but {n_files} clips need "
                f"{n_gaps} (one per gap between consecutive clips)"
            )
        names = [str(t) for t in raw]
    else:
        raise ValueError("concat: 'transitions' must be a string or a list of strings")

    for name in names:
        if name not in CONCAT_TRANSITIONS:
            valid = ", ".join(sorted(CONCAT_TRANSITIONS))
            raise ValueError(f"concat: unknown transition '{name}' (choose from: {valid})")

    if all(name == "none" for name in names):
        return None
    return names


def _concat_with_transitions(
    files: list[str], transitions: list[str], duration: float, out: Path
) -> None:
    """Join clips with a per-gap crossfade (xfade + acrossfade), chained pairwise.

    Each xfade/acrossfade pair overlaps `duration` seconds of the merged stream
    so far with the next clip; a "none" gap uses the `concat` filter instead (a
    hard cut, no overlap). Requires re-encoding (unlike the plain concat
    demuxer), since xfade operates on decoded frames.
    """
    if duration <= 0:
        raise ValueError("concat: transition 'duration' must be > 0")
    durations = [ffmpeg.probe_duration(f) for f in files]
    for i, name in enumerate(transitions):
        if name == "none":
            continue
        left, right = durations[i], durations[i + 1]
        if duration >= left or duration >= right:
            raise ValueError(
                f"concat: transition duration ({duration}s) must be shorter than both clips "
                f"it joins (clip #{i + 1}: {left:.2f}s, clip #{i + 2}: {right:.2f}s)"
            )

    filters: list[str] = []
    cur_v, cur_a = "0:v:0", "0:a:0"
    cur_dur = durations[0]
    for i, name in enumerate(transitions):
        nxt_v, nxt_a = f"{i + 1}:v:0", f"{i + 1}:a:0"
        out_v, out_a = f"vs{i}", f"as{i}"
        if name == "none":
            filters.append(
                f"[{cur_v}][{cur_a}][{nxt_v}][{nxt_a}]concat=n=2:v=1:a=1[{out_v}][{out_a}]"
            )
            cur_dur += durations[i + 1]
        else:
            offset = max(cur_dur - duration, 0.0)
            filters.append(
                f"[{cur_v}][{nxt_v}]xfade=transition={name}:duration={duration}:"
                f"offset={offset:.3f}[{out_v}]"
            )
            filters.append(f"[{cur_a}][{nxt_a}]acrossfade=d={duration}[{out_a}]")
            cur_dur += durations[i + 1] - duration
        cur_v, cur_a = out_v, out_a

    args: list[str] = []
    for f in files:
        args += ["-i", str(f)]
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{cur_v}]",
        "-map",
        f"[{cur_a}]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        str(out),
    ]
    ffmpeg.run(args)


def _concat(files: list[str], out: Path, list_path: Path) -> None:
    # concat demuxer: a text file of `file '<abs path>'` lines, then re-encode
    # (clips share the same export settings, but re-encoding avoids timestamp glitches).
    lines = [f"file '{Path(f).resolve()}'" for f in files]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg.run(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out),
        ]
    )
