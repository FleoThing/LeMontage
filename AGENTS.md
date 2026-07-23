# Working with LeMontage as an AI agent

A playbook for an AI agent turning a user's request into a LeMontage pipeline.
It maps **what the user wants** to **which features to use**. Full block
reference: [docs/SPEC.md](docs/SPEC.md) (§6 per block). Always
`lemontage validate pipeline.yaml` before `run`.

## Golden rule: understand the video before editing it

Do **not** screenshot the video frame by frame and improvise — it burns tokens
and still misses the good moments. Read the video **once** with `analyze`:

```bash
lemontage analyze input.mp4 -o video.vso.json           # shots, loudness, dead air, words
lemontage analyze input.mp4 --visual -o video.vso.json  # + per-shot motion & sharpness (OpenCV)
lemontage analyze input.mp4 --no-transcribe             # skip speech-to-text (faster)
```

The manifest (a Video State Object) gives `duration`, `fps`, `shots[]` (scene
cuts with per-shot `loudness_db`, plus `motion`/`sharpness` 0–1 with `--visual`),
`speech.dead_air` and `speech.words`. Read it, then decide.
`--visual` needs H.264 input (OpenCV can't decode AV1 — transcode first).

## By goal → which features

### General edit / montage / highlight reel (you pick moments visually)
Use the perception layer from **PR #64** (`lemontage analyze`). Read the VSO,
rank shots (high `motion` + decent `sharpness` + loud), pick spans, feed them
back verbatim via `detect_clips: method: agent`:

```yaml
- { id: pick, detect_clips: { method: agent, emit: reel, clips: [ {start: 5.2, end: 7.7}, ... ] } }
- { cut: { from: reel } }
- { export: { from: reel, format: vertical } }
- { id: final, concat: { from: reel, transitions: fade, emit: final } }
```

### "Top X" / best moments by intensity (no editorial reading needed)
`detect_clips: method: loudness` auto-frames the loudest moments (crowd roar,
action, punchlines), expanding around each peak.

```yaml
- { detect_clips: { method: loudness, max_clips: 3, min_duration: 8s, max_duration: 20s, emit: top } }
```

### Podcast clips / quotes (cut on what is *said*)
Get the transcript, read it, cut on whole sentences — never guess off audio:

```bash
lemontage run transcribe.yaml --json > out.json   # an stt step; read cells[].outputs.<id>.words
```

Then feed the chosen spans back with `method: agent` (boundaries are used
verbatim, never snapped):

```yaml
- { detect_clips: { method: agent, emit: clips, clips: [ {start: "1:04", end: "1:39"}, ... ] } }
```

### Music montage (cuts + a track)
Pick punchy shots (see "General edit"), then lay music over the reel. `mix:
false` = music only (drops the source audio); `start_at` skips into the track,
`delay` holds it back, `fade_out` fades the end:

```yaml
- { id: final, concat: { from: reel, transitions: fade, emit: final } }
- { music: { from: final, source: track.mp3, mix: false, start_at: 5s, fade_out: 2s } }
```

### Subtitles / captions
Run `captions` **after** `export` so they're burned at full size on the
reframed (e.g. vertical) clip. Pass `words` from the `stt` step; `style: tiktok`
for karaoke word-highlighting. See §6.5.

### Photo slideshow / carousel (images, not a video)
`input: { type: images, source: ./photos/ }`, then `stills` (one channel item
per image) → `still` (render each into a clip). Add life with Ken Burns motion:
`still: { motion: zoomin | zoomout | panup | pandown, motion_amount: 1.1 }`. See
§6.10–6.11 and `examples/pipeline_carousel.yaml`.

### Slow-motion / fast-forward, or reverse
`speed: { factor: 0.5 }` (slow-mo) or `factor: 2` (fast); `reverse: {}` plays a
clip backwards (boomerang-style). See §6.8–6.9.

### Quick montage, no analysis
Fast paths that need no reading: `detect_clips: method: silence` (keep spoken
spans), `method: scene_change` (cut on visual scene changes), or `method:
random` (seeded, reproducible B-roll).

## Assembling & rendering (building blocks)

- **cut** — extract each channel item's `[start, end]` (§6.4).
- **export** — final render: `format` (`vertical` | `horizontal` | `square`),
  `resolution`, `fps`, per-clip `mute`, and a `title` overlay with
  `{{ part }}` / `{{ index }}` tokens. To avoid black bars on a mismatched
  aspect, use `fit: cover` (crop-to-fill) or `bg: blur` (blurred backdrop, the
  classic vertical look) instead of the default `contain` (§6.6).
- **concat** — stitch a channel into one reel. `transitions:` crossfades each
  gap (`fade`, `fadeblack`, `zoomin`, `circleopen`/`circleclose`, `dissolve`,
  `radial`, slides/wipes — full list in §6.7); `transitions_at: boundaries`
  restricts them to channel-merge joins; `emit:` exposes the reel for a `music`
  step or a parent concat.

## Pipeline plumbing worth using

- **Channels** (`emit:` / `from:`) fan work out per clip in parallel; a
  producer emits a channel, consumers map over it (§8).
- **vars + `--var KEY=VALUE`** — parameterize a reusable pipeline (e.g. swap
  `input.source` or `lang` per run) instead of duplicating files (§3).
- **matrix** — fan the whole pipeline over combinations (e.g. `lang: [fr, en]`)
  for batch output (§9).
- **on_failure** (`abort | skip | retry`) + **retries**, and **requires**
  (gate a step on another's `.success`) for robust longer runs (§5.1).
- **cache** — steps checkpoint by default; a param change reruns the step and
  everything downstream. `run --clean` keeps only the final media.

## Reminders
- Every step needs a **unique `id`** (two anonymous steps of the same block
  collide — validation error).
- `input.source` is `.mp4` only for video; images are a folder.
- Local-first: FFmpeg + faster-whisper, no network after models are cached. No
  cloud providers / TTS / YouTube input yet (see [docs/ROADMAP.md](docs/ROADMAP.md)).
