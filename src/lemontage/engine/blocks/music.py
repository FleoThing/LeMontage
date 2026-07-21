"""``music`` — lay a music track over the final montage (SPEC §6.12).

A channel *aggregator* (``maps = False``), meant to run after ``concat``: it
takes the reel from the channel and muxes an audio file over it. The music is
trimmed (or looped) to the video length, optionally faded out, and mixed with
the video's own audio when it has one.

Alignment: ``align: {drop: auto|<time>, to: <time>}`` shifts the music so its
*drop* (the big energy hit) lands at a chosen point of the video. ``drop: auto``
finds the drop with a windowed-RMS scan — the largest sustained energy jump in
the track — so no manual timing is needed.
"""

from __future__ import annotations

import array
import math
import subprocess
from pathlib import Path
from typing import Any

from .. import ffmpeg, safepath
from ..context import RunContext
from ..timecode import parse_seconds
from .base import Block, BlockResult


class MusicBlock(Block):
    name = "music"
    maps = False

    def execute(self, params: dict[str, Any], ctx: RunContext, step_id: str) -> BlockResult:
        video = params.get("input") or ctx.input.get("source")
        if video is None:
            raise ValueError("music: no input video (use 'from: <channel>' or 'input:')")
        return self._render(params, str(video), ctx)

    def execute_channel(
        self, params: dict[str, Any], items: list[dict[str, Any]], ctx: RunContext, step_id: str
    ) -> BlockResult:
        ordered = sorted(items, key=lambda it: it.get("index", 0))
        files = [it.get("file") or it.get("clip") for it in ordered]
        files = [f for f in files if f]
        if not files:
            return BlockResult(outputs={})
        if len(files) > 1:
            raise ValueError(
                f"music: channel has {len(files)} clips — run 'concat' first so the "
                "music lays over a single video"
            )
        return self._render(params, files[0], ctx)

    def _render(self, params: dict[str, Any], video: str, ctx: RunContext) -> BlockResult:
        source = params.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("music: 'source' (path to an audio file) is required")
        if not Path(source).exists():
            raise ValueError(f"music: source not found: {source}")

        video_dur = ffmpeg.probe_duration(video)
        offset = _music_offset(params, source)
        fade = parse_seconds(params.get("fade_out", 0))
        keep_source_audio = bool(params.get("mix", True))
        out = _output_path(params, ctx)
        _mux(video, source, out, video_dur, offset, fade, keep_source_audio)
        result = str(out)
        return BlockResult(
            outputs={"file": result},
            channel_items=[{"index": 0, "file": result, "clip": result}],
        )


def _music_offset(params: dict[str, Any], source: str) -> float:
    """Seconds into the music where playback starts at video time 0.

    Negative means the music starts *after* the video begins (a delay).
    ``align`` overrides ``start_at``: the drop time minus the video-side target.
    """
    align = params.get("align")
    if align is None:
        return parse_seconds(params.get("start_at", 0))
    if not isinstance(align, dict):
        raise ValueError("music: 'align' must be a mapping with 'drop' and 'to'")
    drop = align.get("drop", "auto")
    drop_at = detect_drop(source) if drop == "auto" else parse_seconds(drop)
    target = parse_seconds(align.get("to", 0))
    return drop_at - target


def _mux(
    video: str,
    source: str,
    out: Path,
    video_dur: float,
    offset: float,
    fade: float,
    keep_source_audio: bool = True,
) -> None:
    """Overlay the music on the video: trim/loop to length, fade out, mix.

    ``keep_source_audio=False`` (``mix: false``) ignores the video's own audio
    and makes the music the sole track — the right choice when the clips are
    muted, since amixing their (silent, concat-spliced) tracks otherwise
    stutters the music at every clip join."""
    chain = []
    if offset > 0:
        chain.append(f"atrim=start={offset:.3f},asetpts=PTS-STARTPTS")
    elif offset < 0:
        chain.append(f"adelay={int(-offset * 1000)}:all=1")
    chain.append(f"atrim=duration={video_dur:.3f}")
    if fade > 0:
        chain.append(f"afade=t=out:st={max(video_dur - fade, 0):.3f}:d={fade:.3f}")
    filters = [f"[1:a]{','.join(chain)}[m]"]

    mix = keep_source_audio and ffmpeg.has_audio(video)
    if mix:
        filters.append("[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")

    args = ["-i", str(video)]
    # Loop the music when it is shorter than the video (after the offset trim).
    if ffmpeg.probe_duration(source) - max(offset, 0) < video_dur:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(source)]
    args += ["-filter_complex", ";".join(filters)]
    args += ["-map", "0:v", "-map", "[a]" if mix else "[m]"]
    args += ["-c:v", "copy", "-c:a", "aac", "-shortest", str(out)]
    ffmpeg.run(args)


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
        out = ctx.output_dir / f"{ctx.pipeline_name}-music.mp4"
    out = safepath.confine(out, safepath.allowed_roots(ctx.output_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


# --- drop detection ----------------------------------------------------------


def detect_drop(source: str | Path, window: float = 0.5, rate: int = 8000) -> float:
    """Find the track's drop: the largest sustained RMS energy jump, in seconds."""
    samples = _decode_pcm(source, rate)
    n = int(rate * window)
    rms = [
        math.sqrt(sum(s * s for s in samples[i : i + n]) / n)
        for i in range(0, len(samples) - n + 1, n)
    ]
    return _drop_window(rms) * window


def _drop_window(rms: list[float]) -> int:
    """Index of the window with the biggest jump vs. what precedes, held ≥3 windows."""
    if len(rms) < 4:
        return 0
    best, best_score = 0, 0.0
    for i in range(1, len(rms) - 2):
        before = sum(rms[max(0, i - 4) : i]) / len(rms[max(0, i - 4) : i])
        sustained = min(rms[i : i + 3])  # must stay loud, not a one-window spike
        score = sustained / (before + 1.0)
        if score > best_score:
            best_score, best = score, i
    return best


def _decode_pcm(source: str | Path, rate: int) -> array.array:
    """Decode the audio to mono 16-bit PCM samples via ffmpeg."""
    cmd = [
        ffmpeg.ffmpeg_bin(),
        "-v",
        "error",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise ffmpeg.FFmpegError(
            f"ffmpeg failed decoding '{source}': {proc.stderr.decode(errors='replace').strip()}"
        )
    raw = proc.stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    return samples
