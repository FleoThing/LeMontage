"""``concat`` — stitch a channel's clips into a single video (SPEC §6.7).

A channel *aggregator* (``maps = False``): instead of running once per item, it
receives the whole channel and joins the clips in order into one file. Place it
after ``export`` to assemble the rendered clips into a final reel.

Plain concatenation uses FFmpeg's concat demuxer (fast, no re-encode of the
joins). Passing ``transitions`` switches to a ``xfade``/``acrossfade``
filter-graph chain instead, crossfading video and audio across each gap.

When ``from`` merges several channels, ``transitions_at: boundaries`` places the
transition only where one channel hands off to the next (a single crossfade at
the viral→montage join), leaving the within-channel gaps as hard cuts. The
default, ``transitions_at: all``, applies to every gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...spec import CONCAT_TRANSITIONS
from .. import ffmpeg, safepath
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
        # Prefer the exported file; fall back to the cut/captioned clip. Keep the
        # item alongside its file so channel boundaries stay aligned after filtering.
        ordered = [it for it in ordered if it.get("file") or it.get("clip")]
        files = [it.get("file") or it.get("clip") for it in ordered]
        if not files:
            return BlockResult(outputs={})
        out = _output_path(params, ctx)
        boundary_gaps = _boundary_gaps(ordered)
        if params.get("transition") is not None:
            names, offsets, duration = _resolve_single_transition(params, len(files), boundary_gaps)
            _concat_with_transitions(files, names, duration, out, offsets)
        else:
            transitions = _resolve_transitions(params, len(files), boundary_gaps)
            if transitions is None:
                _concat(files, out, ctx.work_dir() / f"{step_id}-list.txt")
            else:
                duration = parse_seconds(params.get("duration", _DEFAULT_TRANSITION_DURATION))
                _concat_with_transitions(files, transitions, duration, out)
        # Also expose the reel as a single-item channel: a step with `emit:` can
        # hand its finished clip to a parent concat (nested sub-pipelines).
        reel = str(out)
        return BlockResult(
            outputs={"file": reel, "parts": files},
            channel_items=[{"index": 0, "file": reel, "clip": reel}],
        )


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
    # A pipeline-supplied path must not escape the output tree (path traversal).
    out = safepath.confine(out, safepath.allowed_roots(ctx.output_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _boundary_gaps(ordered: list[dict[str, Any]]) -> list[int]:
    """Gap indices where the source channel changes (a channel-merge boundary).

    Gap ``i`` sits between clip ``i`` and clip ``i + 1``. Items are tagged with
    ``_channel`` by the executor when it merges a list of channels; a single
    channel (or untagged items) yields no boundaries.
    """
    channels = [it.get("_channel") for it in ordered]
    return [
        i
        for i in range(len(channels) - 1)
        if channels[i] is not None and channels[i] != channels[i + 1]
    ]


def _resolve_transitions(
    params: dict[str, Any], n_files: int, boundary_gaps: list[int] | None = None
) -> list[str] | None:
    """Return one transition name per gap between clips, or None for a plain concat.

    ``transitions_at`` scopes where the transition(s) apply: ``all`` (default) is
    one per gap; ``boundaries`` places them only at channel-merge boundaries and
    leaves within-channel gaps as hard cuts.
    """
    raw = params.get("transitions")
    if raw is None:
        return None
    n_gaps = n_files - 1
    if n_gaps <= 0:
        raise ValueError("concat: 'transitions' given but there is only 1 clip to join")

    scope = params.get("transitions_at", "all")
    if scope not in ("all", "boundaries"):
        raise ValueError("concat: 'transitions_at' must be 'all' or 'boundaries'")

    if scope == "boundaries":
        names = _boundary_transition_names(raw, n_gaps, boundary_gaps or [])
    else:
        names = _per_gap_transition_names(raw, n_files, n_gaps)

    for name in names:
        if name not in CONCAT_TRANSITIONS:
            valid = ", ".join(sorted(CONCAT_TRANSITIONS))
            raise ValueError(f"concat: unknown transition '{name}' (choose from: {valid})")

    if all(name == "none" for name in names):
        return None
    return names


def _per_gap_transition_names(raw: object, n_files: int, n_gaps: int) -> list[str]:
    """`transitions_at: all` — a name for every gap (string fills all; list is per-gap)."""
    if isinstance(raw, str):
        return [raw] * n_gaps
    if isinstance(raw, list):
        if len(raw) != n_gaps:
            raise ValueError(
                f"concat: 'transitions' has {len(raw)} entrie(s) but {n_files} clips need "
                f"{n_gaps} (one per gap between consecutive clips)"
            )
        return [str(t) for t in raw]
    raise ValueError("concat: 'transitions' must be a string or a list of strings")


def _boundary_transition_names(raw: object, n_gaps: int, boundary_gaps: list[int]) -> list[str]:
    """`transitions_at: boundaries` — transition only at channel joins, else a hard cut."""
    n_bounds = len(boundary_gaps)
    if n_bounds == 0:
        # Nothing to place transitions on (single channel): fall back to a plain cut.
        return ["none"] * n_gaps
    if isinstance(raw, str):
        at_boundary = [raw] * n_bounds
    elif isinstance(raw, list):
        if len(raw) != n_bounds:
            raise ValueError(
                f"concat: 'transitions_at: boundaries' needs one transition per channel join "
                f"({n_bounds}), got {len(raw)}"
            )
        at_boundary = [str(t) for t in raw]
    else:
        raise ValueError("concat: 'transitions' must be a string or a list of strings")

    names = ["none"] * n_gaps
    for gap, name in zip(boundary_gaps, at_boundary, strict=True):
        names[gap] = name
    return names


def _resolve_single_transition(
    params: dict[str, Any], n_files: int, boundary_gaps: list[int]
) -> tuple[list[str], list[float | None], float]:
    """Resolve the assembly-level ``transition:`` mapping (SPEC §6.7).

    One typed transition placed at the channel-merge boundaries when ``from``
    merges several channels (the part1 → part2 join), or at every gap for a
    single channel. Returns per-gap ``(names, offsets, duration)``; ``offsets``
    carries the optional absolute ``at`` (None = the default clip boundary).
    """
    raw = params.get("transition")
    if not isinstance(raw, dict):
        raise ValueError("concat: 'transition' must be a mapping (type/duration/at)")
    if params.get("transitions") is not None:
        raise ValueError("concat: use either 'transition' or 'transitions', not both")
    n_gaps = n_files - 1
    if n_gaps <= 0:
        raise ValueError("concat: 'transition' given but there is only 1 clip to join")

    name = raw.get("type")
    if not isinstance(name, str) or name not in CONCAT_TRANSITIONS or name == "none":
        valid = ", ".join(sorted(CONCAT_TRANSITIONS - {"none"}))
        raise ValueError(f"concat: unknown transition type '{name}' (choose from: {valid})")
    duration = parse_seconds(raw.get("duration", _DEFAULT_TRANSITION_DURATION))

    gaps = boundary_gaps or list(range(n_gaps))
    names: list[str] = ["none"] * n_gaps
    offsets: list[float | None] = [None] * n_gaps
    for gap in gaps:
        names[gap] = name

    at = raw.get("at")
    if at is not None:
        if len(gaps) != 1:
            raise ValueError(
                "concat: 'transition.at' needs exactly one join (two parts / one boundary), "
                f"got {len(gaps)}"
            )
        offsets[gaps[0]] = parse_seconds(at)
    return names, offsets, duration


def _build_transition_filters(
    durations: list[float],
    transitions: list[str],
    duration: float,
    keep_audio: bool,
    offsets: list[float | None] | None = None,
) -> tuple[list[str], str, str]:
    """Build the pairwise xfade/acrossfade filter chain; returns (filters, out_v, out_a).

    Each xfade/acrossfade pair overlaps `duration` seconds of the merged stream
    so far with the next clip; a "none" gap uses the `concat` filter instead (a
    hard cut, no overlap). An explicit per-gap offset (`transition.at`) starts
    the crossfade at that absolute time in the merged stream instead of at the
    clip boundary, truncating whatever follows it in the left-hand stream.
    """
    if duration <= 0:
        raise ValueError("concat: transition 'duration' must be > 0")
    offsets = offsets or [None] * len(transitions)

    filters: list[str] = []
    cur_v, cur_a = "0:v:0", "0:a:0"
    cur_dur = durations[0]
    for i, name in enumerate(transitions):
        nxt_v, nxt_a = f"{i + 1}:v:0", f"{i + 1}:a:0"
        out_v, out_a = f"vs{i}", f"as{i}"
        if name == "none":
            if keep_audio:
                filters.append(
                    f"[{cur_v}][{cur_a}][{nxt_v}][{nxt_a}]concat=n=2:v=1:a=1[{out_v}][{out_a}]"
                )
            else:
                filters.append(f"[{cur_v}][{nxt_v}]concat=n=2:v=1:a=0[{out_v}]")
            cur_dur += durations[i + 1]
        else:
            right = durations[i + 1]
            if duration >= right:
                raise ValueError(
                    f"concat: transition duration ({duration}s) must be shorter than clip "
                    f"#{i + 2} ({right:.2f}s)"
                )
            at = offsets[i]
            if at is None:
                if duration >= durations[i]:
                    raise ValueError(
                        f"concat: transition duration ({duration}s) must be shorter than clip "
                        f"#{i + 1} ({durations[i]:.2f}s)"
                    )
                offset = max(cur_dur - duration, 0.0)
            else:
                if at < 0 or at + duration > cur_dur:
                    raise ValueError(
                        f"concat: transition 'at' ({at}s) + duration ({duration}s) must fit "
                        f"inside the first part ({cur_dur:.2f}s)"
                    )
                offset = at
            filters.append(
                f"[{cur_v}][{nxt_v}]xfade=transition={name}:duration={duration}:"
                f"offset={offset:.3f}[{out_v}]"
            )
            if keep_audio:
                filters.append(f"[{cur_a}][{nxt_a}]acrossfade=d={duration}[{out_a}]")
            cur_dur = offset + durations[i + 1]
        cur_v, cur_a = out_v, out_a
    return filters, cur_v, cur_a


def _concat_with_transitions(
    files: list[str],
    transitions: list[str],
    duration: float,
    out: Path,
    offsets: list[float | None] | None = None,
) -> None:
    """Join clips with a per-gap crossfade; re-encodes (xfade needs decoded frames)."""
    if duration <= 0:
        raise ValueError("concat: transition 'duration' must be > 0")
    durations = [ffmpeg.probe_duration(f) for f in files]
    # Audio is crossfaded only when EVERY clip has a track; a single silent clip
    # (e.g. a rendered still) drops audio for the whole join rather than failing.
    keep_audio = all(ffmpeg.has_audio(f) for f in files)
    filters, cur_v, cur_a = _build_transition_filters(
        durations, transitions, duration, keep_audio, offsets
    )

    args: list[str] = []
    for f in files:
        args += ["-i", str(f)]
    args += ["-filter_complex", ";".join(filters), "-map", f"[{cur_v}]"]
    if keep_audio:
        args += ["-map", f"[{cur_a}]", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "veryfast", str(out)]
    ffmpeg.run(args)


def _concat_escape(path: str) -> str:
    """Escape a path for a concat-demuxer ``file '…'`` line.

    FFmpeg's concat parser treats ``\\`` as an escape and ``'`` as the string
    delimiter, so a path containing either would break the list (or, with a
    crafted clip path, smuggle in a directive). Backslashes are doubled and each
    single quote is closed/escaped/reopened (``'\\''``).
    """
    return path.replace("\\", "\\\\").replace("'", "'\\''")


def _concat(files: list[str], out: Path, list_path: Path) -> None:
    # concat demuxer: a text file of `file '<abs path>'` lines, then re-encode
    # (clips share the same export settings, but re-encoding avoids timestamp glitches).
    lines = [f"file '{_concat_escape(str(Path(f).resolve()))}'" for f in files]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Keep audio only if every clip has a track; otherwise render video-only
    # (`-an`) so a silent clip in the list doesn't break the mux.
    keep_audio = all(ffmpeg.has_audio(f) for f in files)
    args = [
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
    ]
    args += ["-c:a", "aac"] if keep_audio else ["-an"]
    ffmpeg.run([*args, str(out)])
