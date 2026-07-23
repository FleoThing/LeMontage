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

## Ideas (pool — no commitment, no order)

- **Beat-sync**: lock cuts to the music's strong beats (a beat grid, even on
  irregular tempo), exposed to `method: agent`.
- **Perception++ (VSO)**: sharper visual scoring; `scenedetect` / `silero-vad`
  if FFmpeg's shot splitting / dead-air detection prove too coarse.
- **Vertical smart-crop**: fill the 9:16 frame by following the subject instead
  of black bars — for real TikTok framing.
- **`filter` block**: per-clip looks (black & white, vignette, `eq`, grain,
  sharpen).
- **Full Ken Burns**: horizontal pan / free drift on stills.
- **Observability**: structured logs, run summaries, cache reporting.
- **Long videos**: memory-friendly `reverse`, resumable runs.

### Parked

Local TTS, cloud providers (STT/TTS/LLM), remote inputs (YouTube/URL) and
audio-only input, multiple inputs, hardware encoders
(NVENC/QSV/VideoToolbox/VAAPI).
