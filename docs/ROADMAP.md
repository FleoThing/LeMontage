# Direction & ideas

This is **not a plan or a promise**, and not a history: what has shipped lives
in the [CHANGELOG](../CHANGELOG.md). This document keeps only the **current
direction** and an **idea pool** that grows while building — nothing here is
dated or guaranteed, and ideas get picked when they genuinely help, not because
they are listed.

## Current direction

Make **simple edits excellent** — better than CapCut on the common cases (music
montage, podcast clipping, highlight reel + subtitles), staying **local-first**.

Guiding principle: **the agent must understand the video before editing it.**
Rather than screenshotting frame by frame and improvising (expensive,
imprecise), it reads a compact manifest once (the VSO, via `lemontage analyze`)
— shots, motion, sharpness, loudness, speech, dead air — then decides and feeds
its boundaries back through `detect_clips: method: agent`.

Set aside for now: cloud providers, TTS, remote inputs (YouTube/URL). No
hostility to heavy dependencies — but only when they make a simple edit clearly
better.

## Where it stands

**v0.9.0 was about speed, not features**, and it held its own rule: not one
character of YAML changed. The engine used to parallelise exactly one thing (the
items of a channel); matrix cells, the analysis passes and independent DAG steps
all ran one after another. All three now overlap, underneath a pipeline that is
written and read exactly as before.

| | |
|---|---|
| `analyze`, three ffmpeg passes | -17.7% |
| matrix cells, four-cell control | -17.9% |
| independent DAG steps, real multi-branch pipeline | -6.1% |

Every one of them verified byte-identical against the previous release, with the
controls and the timing script that now live in `benchmarks/`.

Two things measurement changed on the way. The channel worker pool was *not* too
wide at 8 as assumed — sizing it down to the core count measured slower — but it
was capped, so it now grows with the machine. And matrix cells were never
isolated on disk: only the cache was namespaced per cell, never the files.

v0.8.0 finished the caption set (style presets, phrase pop, colour and case);
v0.7.0 finished the short-form clipping set (`zoom`, `sfx`, caption pop,
loudness normalisation).

## Next up

Nothing is committed. The next release gets picked from the pool below when
something in it genuinely helps — the one with the clearest case today is the
transcript cache between `analyze` and `stt`, which was deliberately left out of
v0.9.0 because it changes cache semantics rather than adding concurrency.

## Ideas (pool — no commitment, no order)

- **Perception++ (VSO)**: sharper visual scoring; `scenedetect` / `silero-vad`
  if FFmpeg's shot splitting / dead-air detection prove too coarse.
- **Full Ken Burns**: horizontal pan / free drift on stills.
- **Observability**: structured logs, run summaries, cache reporting.
- **Long videos**: memory-friendly `reverse`, resumable runs.

### Parked

Local TTS, cloud providers (STT/TTS/LLM), remote inputs (YouTube/URL) and
audio-only input, multiple inputs, hardware encoders
(NVENC/QSV/VideoToolbox/VAAPI).
