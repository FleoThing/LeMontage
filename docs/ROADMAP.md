# Roadmap

This roadmap is a planning document. It is not a compatibility promise.
Shipped versions are kept for the record; details live in the CHANGELOG.

## v0.2.0 — shipped

Goal: make LeMontage easier to install, deploy and use for real creator workflows.

Delivered:

- Package and release hardening; `lemontage completion` for bash/zsh/fish.
- Docker and Compose workflows for local runs.
- Production-ready examples under `examples/` (`pipeline_*.yaml`).
- `concat` transitions (crossfade / wipe / slide), `speed` and `reverse` blocks.
- Channel merging and nesting in `concat` (multi-channel `from`,
  `transitions_at`, `emit`); `export` author label.
- Stronger validation messages; documentation split across README,
  contributing, support, security and docs files.

## v0.3.0 – v0.3.3 — shipped

Goal (revised): richer editing primitives, staying local-first.

Delivered:

- Image-folder input (`input.type: images`) with the `stills` / `still`
  blocks: photo slideshows and montages through the existing
  `export`/`concat` chain; audio-tolerant `concat` for video-only clips.
- Mixed media pipelines: images and video clips in the same montage, via
  per-step `input:` overrides and multi-channel `concat`
  (see `examples/pipeline_carousel.yaml`).
- `export` title styling, `fit: contain|cover`, per-clip `mute`, and a
  `random` method for `detect_clips`.
- Hardened paths and FFmpeg inputs (`safepath`, input validation).
- Captions vertical-crop safe area: lines stay readable after a
  `format: vertical, fit: cover` export (0.3.1).
- Documentation site on GitHub Pages (HTML manual for users without `man`);
  preset title fonts pinned to SHA-256 digests at download time; more legible
  default `author_size` (0.3.2).
- FFmpeg/ffprobe subprocess calls no longer leave the terminal unresponsive
  after a run (0.3.3).

## v0.4.0 — shipped

Goal (revised): richer motion and transition primitives for stills-heavy
montages; the rest of the "engine depth" work moved to v0.4.x below.

Delivered:

- Six new `concat` transitions (xfade): `fadeblack`, `zoomin` (FFmpeg ≥ 5.0),
  `circleopen` / `circleclose`, `dissolve`, `radial`.
- Ken Burns motion on stills: `still` `motion: zoomout | zoomin` (eased
  punch-in/out via `zoompan`) and `panup | pandown` (pure vertical scroll via
  a moving crop — no zoom), tuned with `motion_amount` / `motion_duration`.
- Two mapped `export` steps no longer overwrite each other's default outputs
  (custom-id steps now write `<name>-<step_id>-<index>.mp4`).

## v0.4.5 — shipped

Goal: close the AI-agent editing loop, and polish captions/pipeline reuse.

Delivered:

- `detect_clips` `method: agent`: an AI agent reads the transcript and
  supplies exact clip boundaries itself, used verbatim.
- `lemontage run --json`: every step's outputs (notably the `stt`
  transcript) on stdout as JSON, so an agent can transcribe, decide,
  and feed spans back through `detect_clips: method: agent`.
- Bigger, lower default subtitles; captions burned after reframe for
  full-size captions; default `caption_size` 100px.
- Parameterizable `input.source` (via `vars`/`matrix`) for reusable
  pipelines, with checkpoint signatures that account for it.

## v0.4.x — planned: engine depth (continued)

Goal: deepen the local editing engine and make longer projects practical.

Expected work:

- Local TTS behind optional extras (`kokoro-onnx` + `soundfile`), with the
  audio muxing story (voiceover / faceless content) it requires.
- Horizontal pan / free drift on stills (completing the Ken Burns set).
- A `filter` block for per-clip looks — first presets: black & white,
  vignette, `eq` adjustments (brightness / contrast / saturation), film
  grain, sharpen (`feat/filters`).
- Better run observability: structured logs, run summaries, cache reporting.
- More robust long-video workflows (memory-friendly `reverse`, resumable runs).

Definition of done:

- Optional features do not make the core install heavy.
- New features have examples, spec updates and focused tests.

## v0.5.0 — planned: ecosystem

Goal: open the ecosystem layer while keeping the core engine local-first.

Expected work:

- Optional provider interfaces for cloud STT, TTS and LLM services.
- Community pipeline metadata and contribution rules.
- Remote inputs (URLs / YouTube) and audio-only input, currently reserved
  in the spec.
- Multiple inputs per pipeline.
- Hardware acceleration: detect the available FFmpeg hardware encoders at
  run time (NVENC / QSV / VideoToolbox / VAAPI) and prefer them with a
  silent `libx264` fallback, plus a global setting to force software or a
  specific encoder (hardware encoders trade some compression efficiency
  for speed). Route the encoder choice through one place in the engine
  instead of the per-block `libx264` literals. Let the Whisper STT
  provider run on CUDA when present (`device: auto`).

Definition of done:

- Provider additions remain optional; pipelines stay portable and reviewable.
- Reserved spec keys are either implemented or explicitly re-scoped.
