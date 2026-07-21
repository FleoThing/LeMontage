"""``detect_clips`` — find candidate clips and emit them as a channel (SPEC §6.3).

Four local methods:

* ``silence``      — split on silence (``silencedetect``), keep the spoken spans.
* ``scene_change`` — split on visual scene cuts (``select='gt(scene,…)'``).
* ``loudness``     — rank moments by audio loudness (``ebur128``) and keep the
  loudest, centred on each peak — the best local proxy for action/highlights
  (crowd roar + commentator excitement).
* ``random``       — pick random, non-overlapping moments (no analysis); handy
  for a quick montage or B-roll. A ``seed`` makes it reproducible.
* ``audio``        — cut to the rhythm of a music track: track beats
  (onset envelope + a tempo grid that fills soft/missing beats and rejects
  off-beat noise) and emit one clip per beat-to-beat span — automatically-timed
  jump cuts that stay locked to the pulse even when the rhythm is irregular.
  ``music:`` points at the track (defaults to the input's own audio);
  ``min_gap`` sets the shortest cut.

``silence`` and ``scene_change`` then trim/split the spans to the
``[min_duration, max_duration]`` window and cap at ``max_clips``; ``loudness``
emits centred clips directly, and ``random`` scatters clips across the timeline.
"""

from __future__ import annotations

import math
import random
import re
import statistics
from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult

_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")
_SCENE_PTS = re.compile(r"pts_time:([\d.]+)")
# astats+ametadata prints two lines per window: "…pts_time:<sec>" then
# "…RMS_level=<dB>". DOTALL lets the regex pair each timestamp with its level.
_LOUDNESS = re.compile(r"pts_time:([\d.]+).*?RMS_level=(-?[\d.]+|-inf)", re.DOTALL)
# Auto-framing of a loud moment. The clip boundaries are found by expanding from
# the peak while the level stays above a threshold set between the baseline
# (median) and the peak — so the build-up and the sustained reaction are both
# captured without any manual offset.
_LOUD_RISE = 0.5  # threshold = baseline + this * (peak - baseline)
_LEAD_IN = 3.0  # seconds of context kept before the loud onset
_GAP_TOLERANCE = 2  # windows below threshold tolerated before stopping expansion


class DetectClipsBlock(Block):
    name = "detect_clips"

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        media = params.get("input") or ctx.input.get("source")
        if not media:
            raise ValueError("detect_clips: no input media")
        method = params.get("method", "silence")
        min_dur = parse_seconds(params.get("min_duration", "15s"))
        max_dur = parse_seconds(params.get("max_duration", "60s"))
        max_clips = int(params.get("max_clips", 5))

        total = ffmpeg.probe_duration(media)
        if method == "agent":
            clips = _agent_clips(params.get("clips"), total)
        elif method == "loudness":
            timeline = _loudness_timeline(media)
            clips = _select_loud_clips(timeline, total, min_dur, max_dur, max_clips)
        elif method == "silence":
            spans = _speech_spans_from_silence(media, total)
            clips = _windowed_clips(spans, min_dur, max_dur, max_clips)
        elif method == "scene_change":
            spans = _spans_from_scene_cuts(media, total)
            clips = _windowed_clips(spans, min_dur, max_dur, max_clips)
        elif method == "audio":
            music = params.get("music") or media
            min_gap = parse_seconds(params.get("min_gap", "0.5s"))
            beats = _detect_beats(_loudness_timeline(music, nsamples=512), min_gap)
            clips = _beat_clips(beats, total, max_clips)
        elif method == "random":
            rng = random.Random(params.get("seed"))
            clips = _random_clips(total, min_dur, max_dur, max_clips, rng)
        else:
            raise ValueError(f"detect_clips: unsupported method '{method}'")

        words = params.get("words") or []
        # In agent mode the boundaries are the agent's decision — attach the
        # transcript for context but never snap them. For detected methods,
        # snapping trims machine-guessed boundaries to whole words.
        snap = method != "agent"
        items = [
            _clip_item(i, start, end, words, snap=snap) for i, (start, end) in enumerate(clips)
        ]

        return BlockResult(
            outputs={
                "count": len(items),
                "timestamps": [{"start": it["start"], "end": it["end"]} for it in items],
                # Full items (with per-clip `text`/`words` when a transcript was
                # given) so an AI agent reading `--json` can refine boundaries.
                "clips": items,
            },
            channel_items=items,
        )


def _agent_clips(clips: Any, total: float) -> list[tuple[float, float]]:
    """``method: agent`` — the AI agent supplies the boundaries itself.

    Each entry is ``{start, end}`` (any time value: ``"1:05"``, ``65``, ``"65s"``).
    The agent has already read the transcript, so its picks are used verbatim —
    only clamped to the media and dropped if empty. No detection, no min/max, no
    ``max_clips`` cap: full control stays with the agent.
    """
    if not isinstance(clips, list) or not clips:
        raise ValueError("detect_clips: method 'agent' requires a non-empty 'clips' list")
    out: list[tuple[float, float]] = []
    for c in clips:
        start = max(0.0, parse_seconds(c["start"]))
        end = min(total, parse_seconds(c["end"]))
        if end > start:
            out.append((start, end))
    return out


def _clip_item(
    index: int, start: float, end: float, words: list[dict[str, Any]], snap: bool = True
) -> dict[str, Any]:
    """Build a channel item, attaching the spoken text and (when ``snap``)
    trimming boundaries to whole words — so an AI agent can pick precise
    start/end from the clip's own transcript instead of guessing off audio/scene
    boundaries alone. ``snap=False`` keeps the given boundaries verbatim."""
    inside = [w for w in words if w.get("end", 0) > start and w.get("start", 0) < end]
    # Trim inward to whole words: begin at the first word onset >= start, end at
    # the last word offset <= end. Never cut a word in half.
    whole = [w for w in inside if w["start"] >= start and w["end"] <= end]
    if snap and whole:
        start, end = whole[0]["start"], whole[-1]["end"]
    item: dict[str, Any] = {"index": index, "start": round(start, 3), "end": round(end, 3)}
    if inside:
        item["text"] = " ".join(w.get("text", "") for w in inside).strip()
        item["words"] = inside
    return item


def _speech_spans_from_silence(media: str, total: float) -> list[tuple[float, float]]:
    stderr = ffmpeg.run_capture(
        ["-i", str(media), "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]
    )
    starts = [float(m) for m in _SILENCE_START.findall(stderr)]
    ends = [float(m) for m in _SILENCE_END.findall(stderr)]

    # Speech = the gaps between silences, bounded by [0, total].
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in zip(starts, ends, strict=False):
        if s_start > cursor:
            spans.append((cursor, s_start))
        cursor = s_end
    if cursor < total:
        spans.append((cursor, total))
    return spans or [(0.0, total)]


def _spans_from_scene_cuts(media: str, total: float) -> list[tuple[float, float]]:
    stderr = ffmpeg.run_capture(
        [
            "-i",
            str(media),
            "-vf",
            "select='gt(scene,0.3)',showinfo",
            "-f",
            "null",
            "-",
        ]
    )
    cuts = sorted({float(m) for m in _SCENE_PTS.findall(stderr)})
    boundaries = [0.0, *cuts, total]
    return [(a, b) for a, b in zip(boundaries, boundaries[1:], strict=False) if b > a]


def _windowed_clips(
    spans: list[tuple[float, float]], min_dur: float, max_dur: float, max_clips: int
) -> list[tuple[float, float]]:
    clips: list[tuple[float, float]] = []
    for start, end in spans:
        cursor = start
        while end - cursor >= min_dur:
            clip_end = min(cursor + max_dur, end)
            clips.append((cursor, clip_end))
            cursor = clip_end
            if len(clips) >= max_clips:
                return clips
    return clips


def _random_clips(
    total: float, min_dur: float, max_dur: float, max_clips: int, rng: random.Random
) -> list[tuple[float, float]]:
    """Pick random moments, each one *further along* the video than the last.

    The timeline is split into ``n`` successive equal segments and one random
    clip (a random length within ``[min_dur, max_dur]``) is placed inside each,
    at a random position. So every clip advances past the previous one and no
    moment is ever picked twice — a forward walk with random spots. Pass a
    ``seed`` for reproducible runs.
    """
    if total <= 0 or min_dur <= 0 or total < min_dur or max_clips <= 0:
        return []
    n = min(max_clips, int(total // min_dur))
    slot = total / n  # each clip lives in its own forward slice of the timeline

    clips: list[tuple[float, float]] = []
    for i in range(n):
        length = rng.uniform(min_dur, min(max_dur, slot))
        start = i * slot + rng.uniform(0.0, slot - length)
        clips.append((start, start + length))
    return clips


def _loudness_timeline(media: str, nsamples: int = 8000) -> list[tuple[float, float]]:
    """Return [(time, RMS level in dB)] sampled in windows of ``nsamples``
    samples at 8 kHz (default 8000 = 1-second windows; 512 = 64 ms, fine
    enough to resolve musical beats). Silent windows read as ``-inf`` dB."""
    stderr = ffmpeg.run_capture(
        [
            "-i",
            str(media),
            "-af",
            (
                "aformat=channel_layouts=mono,aresample=8000,"
                f"asetnsamples=n={nsamples}:p=0,astats=metadata=1:reset=1,"
                "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level"
            ),
            "-f",
            "null",
            "-",
        ]
    )
    return [(float(t), float(level)) for t, level in _LOUDNESS.findall(stderr)]


_BEAT_RISE = 6.0  # dB above the moving average that marks a beat onset
_BEAT_HISTORY = 8  # windows in the moving average (~0.5 s at 64 ms windows)
# Tempo-aware grid: only engages when there are enough onsets to trust a period.
_GRID_MIN_ONSETS = 5  # below this, sparse audio stays on the raw-onset path
_TEMPO_MIN_BPM = 60.0  # slowest tempo the grid will lock to
_TEMPO_MAX_BPM = 200.0  # fastest tempo the grid will lock to
_GRID_TOL = 0.30  # snap a grid beat to a real onset within ±this fraction of a period


def _detect_beats(
    timeline: list[tuple[float, float]], min_gap: float, rise: float = _BEAT_RISE
) -> list[float]:
    """Beat tracking, robust to irregular rhythm.

    Two stages. First, *raw onsets*: a window whose RMS jumps ``rise`` dB above
    the moving average of the previous windows, ``min_gap`` apart. On sparse
    audio (fewer than ``_GRID_MIN_ONSETS`` onsets) these are returned as-is.

    On a dense, periodic stream the second stage takes over: estimate the
    dominant *period* by autocorrelating the onset envelope, phase-lock a grid
    to the real onsets, then walk the grid across the track. Each grid beat
    snaps to the nearest real onset when one sits within ``_GRID_TOL`` of a
    period; otherwise the grid position itself is emitted — so a beat that was
    too soft to clear the threshold (the ``da di`` in *do do do da di do*) is
    filled in, and a spurious onset off the grid is dropped. The cuts stay
    locked to the pulse instead of chasing every wobble in the mix.

    Stdlib-only; strict ``>`` keeps pure silence (-inf vs -inf) from registering.
    """
    onsets = _raw_onsets(timeline, min_gap, rise)
    if len(onsets) < _GRID_MIN_ONSETS:
        return onsets

    env = _onset_envelope(timeline)
    dt = _window_dt(timeline)
    period = _dominant_period(env, dt)
    if period is None:
        return onsets
    return _track_grid(timeline, env, dt, period, onsets, min_gap)


def _raw_onsets(
    timeline: list[tuple[float, float]], min_gap: float, rise: float
) -> list[float]:
    """A beat per window that jumps ``rise`` dB above the trailing average."""
    beats: list[float] = []
    for i, (t, level) in enumerate(timeline):
        history = [lvl for _, lvl in timeline[max(0, i - _BEAT_HISTORY) : i]]
        if not history:
            continue
        avg = sum(history) / len(history)
        if level > avg + rise and (not beats or t - beats[-1] >= min_gap):
            beats.append(t)
    return beats


def _window_dt(timeline: list[tuple[float, float]]) -> float:
    """Seconds per analysis window (the sampling period of the envelope)."""
    return timeline[1][0] - timeline[0][0] if len(timeline) > 1 else 0.064


def _onset_envelope(timeline: list[tuple[float, float]]) -> list[float]:
    """Positive RMS increase over the trailing average, per window — an onset
    strength signal that peaks on attacks and is ~0 on sustains and silence."""
    env: list[float] = []
    for i, (_t, level) in enumerate(timeline):
        history = [lvl for _, lvl in timeline[max(0, i - _BEAT_HISTORY) : i]]
        avg = sum(history) / len(history) if history else level
        flux = level - avg
        env.append(flux if (flux > 0 and math.isfinite(flux)) else 0.0)
    return env


def _dominant_period(env: list[float], dt: float) -> float | None:
    """Dominant beat period in seconds, via autocorrelation of the onset
    envelope over the ``[_TEMPO_MIN_BPM, _TEMPO_MAX_BPM]`` lag range. Returns
    None when no lag stands out (no steady pulse to lock to)."""
    min_lag = max(1, int(round(60.0 / _TEMPO_MAX_BPM / dt)))
    max_lag = min(len(env) - 1, int(round(60.0 / _TEMPO_MIN_BPM / dt)))
    if max_lag <= min_lag:
        return None
    best_lag, best_score = 0, 0.0
    for lag in range(min_lag, max_lag + 1):
        score = sum(env[i] * env[i - lag] for i in range(lag, len(env)))
        if score > best_score:
            best_score, best_lag = score, lag
    return best_lag * dt if best_lag else None


def _track_grid(
    timeline: list[tuple[float, float]],
    env: list[float],
    dt: float,
    period: float,
    onsets: list[float],
    min_gap: float,
) -> list[float]:
    """Phase-lock a grid of spacing ``period`` to the onsets, then emit one beat
    per grid step — snapped to a real onset when one is within tolerance, else
    the grid position itself (a filled-in soft/missing beat)."""
    end = timeline[-1][0]
    phase = _grid_phase(env, dt, period)
    tol = _GRID_TOL * period

    beats: list[float] = []
    g = phase
    while g <= end + 1e-9:
        near = min((o for o in onsets if abs(o - g) <= tol), key=lambda o: abs(o - g), default=g)
        beat = near
        if beat >= 0 and (not beats or beat - beats[-1] >= min_gap):
            beats.append(beat)
        g += period
    return beats


def _grid_phase(env: list[float], dt: float, period: float) -> float:
    """Grid offset (seconds) whose beat positions collect the most onset energy —
    aligns the grid to the actual downbeats rather than an arbitrary t=0."""
    step = max(1, int(round(period / dt)))
    best_off, best_energy = 0, -1.0
    for off in range(step):
        energy = sum(env[i] for i in range(off, len(env), step))
        if energy > best_energy:
            best_energy, best_off = energy, off
    return best_off * dt


def _beat_clips(beats: list[float], total: float, max_clips: int) -> list[tuple[float, float]]:
    """One clip per beat-to-beat span across the source, starting at 0 and
    ending at the media end — jump cuts timed by the music."""
    edges = [0.0, *(b for b in beats if 0.0 < b < total), total]
    return [(a, b) for a, b in zip(edges, edges[1:], strict=False) if b > a][:max_clips]


def _select_loud_clips(
    timeline: list[tuple[float, float]],
    total: float,
    min_dur: float,
    max_dur: float,
    max_clips: int,
) -> list[tuple[float, float]]:
    """Auto-frame the loudest moments: each clip spans its own loud region.

    Boundaries are found by expanding from a peak while the level stays above a
    threshold between the baseline (median) and the peak, then padded with a
    short lead-in/out and clamped to ``[min_dur, max_dur]``. No manual tuning:
    the build-up and the sustained reaction fall out of the audio envelope.
    """
    if not timeline:
        return []
    times = [t for t, _ in timeline]
    levels = [level for _, level in timeline]
    baseline = statistics.median(levels)
    peak_level = max(levels)
    if peak_level <= baseline:
        return []
    threshold = baseline + _LOUD_RISE * (peak_level - baseline)

    chosen: list[tuple[float, float]] = []
    for i in sorted(range(len(levels)), key=lambda j: levels[j], reverse=True):
        if levels[i] < threshold:  # only genuinely loud moments qualify
            break
        onset = _loud_onset(levels, i, threshold)
        # Anchor just before the loud onset, then run forward for the requested
        # length: the reaction (visually interesting but quieter) is kept too.
        start = max(0.0, times[onset] - _LEAD_IN)
        end = min(total, start + max_dur)
        if end - start < min_dur:  # only near the very end of the media
            start = max(0.0, end - min_dur)
        if any(start < ce and end > cs for cs, ce in chosen):  # overlaps a pick
            continue
        chosen.append((start, end))
        if len(chosen) >= max_clips:
            break
    return sorted(chosen)


def _loud_onset(levels: list[float], peak: int, threshold: float) -> int:
    """Walk left from the peak to the start of its loud burst."""
    onset = peak
    misses = 0
    j = peak - 1
    while j >= 0:
        if levels[j] >= threshold:
            onset, misses = j, 0
        else:
            misses += 1
            if misses > _GAP_TOLERANCE:
                break
        j -= 1
    return onset
