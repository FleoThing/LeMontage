"""``music`` — lay a music track over the final montage (SPEC §6.12).

A channel *aggregator* (``maps = False``), meant to run after ``concat``: it
takes the reel from the channel and muxes an audio file over it. The music is
trimmed (or looped) to the video length, optionally faded out, and mixed with
the video's own audio when it has one.

Timing: ``start_at`` skips into the track (drop the song's intro); ``delay``
holds the music back so it enters later over the video (silence first). The two
are independent and compose.
"""

from __future__ import annotations

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
        start_at = parse_seconds(params.get("start_at", 0))
        delay = parse_seconds(params.get("delay", 0))
        fade = parse_seconds(params.get("fade_out", 0))
        keep_source_audio = bool(params.get("mix", True))
        out = _output_path(params, ctx)
        _mux(video, source, out, video_dur, start_at, delay, fade, keep_source_audio)
        result = str(out)
        return BlockResult(
            outputs={"file": result},
            channel_items=[{"index": 0, "file": result, "clip": result}],
        )


def _mux(
    video: str,
    source: str,
    out: Path,
    video_dur: float,
    start_at: float,
    delay: float,
    fade: float,
    keep_source_audio: bool = True,
) -> None:
    """Overlay the music on the video: skip in, hold back, trim/loop, fade, mix.

    ``start_at`` trims that many seconds off the front of the track. ``delay``
    pads silence in front so the music enters ``delay`` seconds into the video;
    the music then only has to fill the remaining ``video_dur - delay`` seconds.

    ``keep_source_audio=False`` (``mix: false``) ignores the video's own audio
    and makes the music the sole track — the right choice when the clips are
    muted, since amixing their (silent, concat-spliced) tracks otherwise
    stutters the music at every clip join."""
    play_dur = max(0.0, video_dur - delay)
    chain = []
    if start_at > 0:
        chain.append(f"atrim=start={start_at:.3f},asetpts=PTS-STARTPTS")
    chain.append(f"atrim=duration={play_dur:.3f}")
    if delay > 0:
        chain.append(f"adelay={int(delay * 1000)}:all=1")
    if fade > 0:
        chain.append(f"afade=t=out:st={max(video_dur - fade, 0):.3f}:d={fade:.3f}")
    filters = [f"[1:a]{','.join(chain)}[m]"]

    mix = keep_source_audio and ffmpeg.has_audio(video)
    if mix:
        filters.append("[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")

    args = ["-i", str(video)]
    # Loop the music when it can't fill the video after the start_at trim.
    if ffmpeg.probe_duration(source) - start_at < play_dur:
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
