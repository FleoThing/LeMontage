"""``detect_clips`` — find candidate clips and emit them as a channel (SPEC §6.3).

Local methods:

* ``silence``      — split on silence (``silencedetect``), keep the spoken spans.
* ``scene_change`` — split on visual scene cuts (``select='gt(scene,…)'``).
* ``loudness``     — rank moments by audio loudness (``ebur128``) and keep the
  loudest, centred on each peak — the best local proxy for action/highlights
  (crowd roar + commentator excitement).
* ``random``       — pick random, non-overlapping moments (no analysis); handy
  for a quick montage or B-roll. A ``seed`` makes it reproducible.
* ``beat``         — tile the source into segments whose boundaries land on a
  music track's beats (``librosa``, ``[beat]`` extra), so a concatenated reel
  cuts on the beat. See ``_beat_times`` / ``_beat_clips``.

``silence`` and ``scene_change`` then trim/split the spans to the
``[min_duration, max_duration]`` window and cap at ``max_clips``; ``loudness``
emits centred clips directly, and ``random`` scatters clips across the timeline.
"""

from __future__ import annotations

import itertools
import random
import re
import statistics
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult

_SILENCE_DB = -30.0  # below this level counts as silence
_SILENCE_GAP = 0.5  # …for at least this long (seconds)

_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")
_SCENE_PTS = re.compile(r"pts_time:([\d.]+)")
# astats+ametadata prints two lines per window: "…pts_time:<sec>" then
# "…RMS_level=<dB>". DOTALL lets the regex pair each timestamp with its level.
_LOUDNESS = re.compile(r"pts_time:([\d.]+).*?RMS_level=(-?[\d.]+)", re.DOTALL)
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
        beats: list[float] = []
        if method == "agent":
            clips = _agent_clips(params.get("clips"), total)
        elif method == "beat":
            beats = _beat_times(params.get("track"), parse_seconds(params.get("start_at", 0)))
            clips = _beat_clips(
                beats,
                total,
                int(params.get("beats_per_clip", 4)),
                max_clips,
                parse_seconds(params.get("source_start", 0)),
            )
        elif method == "loudness":
            timeline = _loudness_timeline(media)
            clips = _select_loud_clips(timeline, total, min_dur, max_dur, max_clips)
        elif method == "silence":
            spans = _speech_spans_from_silence(
                media,
                total,
                noise_db=float(params.get("silence_db", _SILENCE_DB)),
                gap=parse_seconds(params.get("silence_gap", _SILENCE_GAP)),
            )
            clips = _windowed_clips(spans, min_dur, max_dur, max_clips)
        elif method == "scene_change":
            spans = _spans_from_scene_cuts(media, total)
            clips = _windowed_clips(spans, min_dur, max_dur, max_clips)
        elif method == "random":
            rng = random.Random(params.get("seed"))
            clips = _random_clips(total, min_dur, max_dur, max_clips, rng)
        else:
            raise ValueError(f"detect_clips: unsupported method '{method}'")

        words = params.get("words") or []
        # In agent/beat mode the boundaries are deliberate (the agent's picks, or
        # a musical beat grid) — attach the transcript for context but never snap
        # them. For detected methods, snapping trims machine-guessed boundaries to
        # whole words.
        snap = method not in ("agent", "beat")
        items = [
            _clip_item(i, start, end, words, snap=snap) for i, (start, end) in enumerate(clips)
        ]

        outputs: dict[str, Any] = {
            "count": len(items),
            "timestamps": [{"start": it["start"], "end": it["end"]} for it in items],
            # Full items (with per-clip `text`/`words` when a transcript was
            # given) so an AI agent reading `--json` can refine boundaries.
            "clips": items,
        }
        # Expose the raw beat grid so `method: agent` can pick beat-aligned
        # boundaries itself (roadmap: beat-sync exposed to the agent loop).
        if beats:
            outputs["beats"] = [round(t, 3) for t in beats]
        return BlockResult(outputs=outputs, channel_items=items)


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


def _beat_times(track: Any, start_at: float) -> list[float]:
    """Detect a music track's beat times (seconds) via ``librosa``.

    Uses PLP (predominant local pulse) rather than a single global BPM, so the
    grid follows tempo drift (accelerando, live drums) instead of drifting off a
    fixed grid — the whole point of beat-sync on real music. ``start_at`` drops
    beats before that offset (align with the ``music`` block's own ``start_at``).
    """
    if not isinstance(track, str) or not track:
        raise ValueError("detect_clips: method 'beat' requires a 'track' (path to a music file)")
    if not Path(track).exists():
        raise ValueError(f"detect_clips: beat track not found: {track}")
    try:
        import librosa
        import numpy as np
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ValueError(
            "detect_clips: method 'beat' needs librosa — install with pip install 'lemontage[beat]'"
        ) from exc

    y, sr = librosa.load(track, sr=None, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    pulse = librosa.beat.plp(onset_envelope=onset_env, sr=sr)
    beat_frames = np.flatnonzero(librosa.util.localmax(pulse))
    times = librosa.frames_to_time(beat_frames, sr=sr)
    return [float(t) for t in times if t >= start_at]


def _beat_clips(
    beats: list[float],
    total: float,
    beats_per_clip: int,
    max_clips: int,
    source_start: float = 0.0,
) -> list[tuple[float, float]]:
    """Beat-driven montage clips: cut *rhythm* follows the beats, cut *content*
    jumps around the source so every beat is a visible change of shot.

    Two independent things:

    * **Length** — clip *i* lasts ``beats_per_clip`` beats' worth of time (a bar,
      by default 4). The cumulative lengths equal the beat grid, so the
      concatenated reel's cut points land on the beats (no min/max clamp, which
      would desync them).
    * **Position** — clip *i* is drawn from a *different, spread-out* part of the
      source (``[source_start, total]`` split into equal slots). Taking
      consecutive segments instead would replay the footage continuously and show
      no cut at all on the beat — the whole point is that each beat jumps to a new
      moment. Stops at ``max_clips``.
    """
    k = max(1, beats_per_clip)
    marks = beats[::k]
    durations = [b - a for a, b in itertools.pairwise(marks) if b > a]
    durations = durations[:max_clips]
    if not durations:
        return []
    span = max(0.0, total - source_start)
    slot = span / len(durations)  # each clip starts in its own slice of the source
    clips: list[tuple[float, float]] = []
    for i, d in enumerate(durations):
        start = source_start + i * slot
        end = min(start + d, total)
        if end - start >= 0.1:  # skip a degenerate tail at the very end
            clips.append((start, end))
    return clips


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


def _speech_spans_from_silence(
    media: str, total: float, noise_db: float = _SILENCE_DB, gap: float = _SILENCE_GAP
) -> list[tuple[float, float]]:
    """The spoken spans: everything `silencedetect` did not call silence.

    ``noise_db``/``gap`` are the detector's two knobs, and they decide how tight
    the edit is: the defaults only drop a real pause, while ``gap: 0.25`` also
    removes the breaths between sentences (the jump-cut look) and a higher
    ``noise_db`` treats a noisy room as silence.
    """
    stderr = ffmpeg.run_capture(
        [
            "-i",
            str(media),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={gap:.3f}",
            "-f",
            "null",
            "-",
        ]
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
    return [(a, b) for a, b in itertools.pairwise(boundaries) if b > a]


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


def _loudness_timeline(media: str) -> list[tuple[float, float]]:
    """Return [(time, RMS level in dB)] sampled in 1-second windows."""
    stderr = ffmpeg.run_capture(
        [
            "-i",
            str(media),
            "-af",
            (
                "aformat=channel_layouts=mono,aresample=8000,"
                "asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,"
                "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level"
            ),
            "-f",
            "null",
            "-",
        ]
    )
    return [(float(t), float(level)) for t, level in _LOUDNESS.findall(stderr)]


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
