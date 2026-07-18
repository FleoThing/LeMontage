# Changelog

All notable changes to LeMontage are tracked here.

This project follows SemVer-style versioning while it is pre-1.0: minor versions
may still introduce breaking changes, and those changes must be called out here.

## [Unreleased]

## [0.4.0] - 2026-07-18

### Added

- Six new `concat` transitions: `fadeblack` (fade through black, for a marked
  scene break), `zoomin` (dynamic push, needs FFmpeg >= 5.0), `circleopen` /
  `circleclose` (spotlight iris), `dissolve` (noisy organic fade) and `radial`
  (clock-hand sweep).
- `still` motion effects: `motion: zoomout | zoomin` animates each image with
  an eased punch-out / punch-in (fast start, braking before it lands), and
  `motion: panup | pandown` is a pure vertical scroll — a full-width band
  slides across the image at constant speed, no zoom. Tuned via
  `motion_amount` (default 1.1) and `motion_duration` (default: the whole
  clip). See `examples/pipeline_zoom_punch.yaml` and
  `examples/pipeline_pan_scroll.yaml`.

- `detect_clips` `method: agent`: an AI agent reads the transcript
  (`words` from `stt`) and supplies exact `clips: [{start, end}]` itself,
  used verbatim — no heuristic. Every method now attaches spoken
  `text`/`words` to each candidate so an agent sees what is said.
- `lemontage run --json`: prints every step's outputs (notably the `stt`
  transcript with word timings) to stdout as JSON, status lines stay on
  stderr. Closes the AI-agent loop: transcribe, read the transcript,
  decide which spans are viral, feed them back through
  `detect_clips: method: agent`.
- Bigger, lower default subtitles (`caption_size` 100px, lower
  `caption_margin`); `captions` prefers the exported `file` over the cut
  `clip`, so placing it after `export` burns captions at full size on the
  reframed (e.g. vertical) clip. Adds an `output:` param so `captions`
  can be the last step.
- Parameterizable `input.source` (via `vars`/`matrix`): a single pipeline
  can take its source via `--var` instead of duplicating the file per
  video.

### Fixed

- `detect_clips` agent boundaries are used verbatim instead of snapping
  to `words:`, and are exposed via a `clips` output so the
  refine-detected-clips agent loop works end to end.
- Checkpoint signatures now include `input.source`, so two different
  input videos with identical step params no longer collide on the same
  cache entry.

- Two `export` steps in the same pipeline no longer overwrite each other's
  clips: without an explicit `output:`, a custom-id export step now writes
  `<name>-<step_id>-<index>.mp4` instead of the shared `<name>-<index>.mp4`.
  Pipelines with a single (implicit-id) export step keep the historical
  naming.

## [0.3.3] - 2026-07-08

### Fixed

- FFmpeg/ffprobe subprocess calls now redirect stdin to `/dev/null`: without
  `-nostdin`, ffmpeg put the controlling terminal in raw/no-echo mode to
  listen for keypresses and didn't reliably restore it, leaving the terminal
  unresponsive after a pipeline run finished.

## [0.3.2] - 2026-07-07

### Changed

- Default `author_size` of the export author label raised from 26 to 44 px:
  26 was barely legible once compressed by the Shorts/TikTok players.

### Security

- Preset title fonts (`font1`-`font5`) are now verified against pinned SHA-256
  digests at download time: a substituted TTF (MITM, compromised upstream) is
  rejected before ever reaching libass (audit S6, #35).


### Added

- Documentation site on GitHub Pages: the man page and the Markdown docs are
  rendered to HTML on every docs push to main — for users without `man`
  (Windows).

## [0.3.1] - 2026-07-07

### Fixed

- `captions` on a landscape source no longer lose their line ends when the clip
  is later exported vertical with `fit: cover`: lines are kept inside the centre
  9:16 column (and wrap instead of overflowing). Opt out with `safe_area: false`
  when the final export stays horizontal.

## [0.3.0] - 2026-07-07

### Added

- Image-folder input (`input.type: images`): build a slideshow / photo montage
  from a folder of `.jpg` / `.jpeg` / `.png` / `.webp` files.
- `stills` built-in block: emit a channel with one item per image of a folder
  (natural sort, optional seeded `shuffle`, `max`, per-image `duration`).
- `still` built-in block: render an image into a short video-only H.264 clip so
  `export` and `concat` (transitions) can treat it like any other clip.
- `concat` tolerates video-only clips: when a clip has no audio track, the join
  is rendered without audio instead of failing.

## [0.2.0] - 2026-07-06

### Added

- `concat` can merge several channels: `from: [viral, montage]` joins channels
  in listed order into one reel. `transitions_at: boundaries` places a single
  transition at each channel join (default `all` crossfades every gap).
- `concat` can `emit:` its reel as a channel, so branches nest: each is a
  self-contained sub-pipeline concatenating (with its own transitions) into one
  clip, and a parent `concat` joins those clips — with or without a transition.
- `export` author label: persistent corner credit for the clip's source channel
  or the editor's own handle (`author`, `author_position`, `author_size`,
  `author_margin`, `author_font`).
- `lemontage completion <shell>` command: bash, zsh and fish completion scripts.
- `concat` transitions: crossfade / wipe / slide between clips via `transitions` and `duration`.
- `reverse` built-in block: play a clip backwards (video + audio).
- `speed` built-in block: slow-motion / fast-forward by a playback factor.
- Project documentation split across README, contributing, support, security and docs files.
- Docker Compose local deployment file under `infrastructure/local/compose.yaml`.
- Documentation assets grouped under `docs/assets/`.

### Changed

- Installation scripts moved under `infrastructure/script/`.
- README reorganized around app description, tech stack and install/deploy options.
- **Breaking:** a step's `output:` path must now resolve inside the pipeline's
  output directory or the current working directory; absolute paths or `..`
  traversal that escape both are rejected.

### Security

- Confine `export` and `concat` output paths to the allowed output tree,
  preventing a shared pipeline from writing files to arbitrary locations.
- Escape ASS override syntax (`{`, `}`, `\`) in caption text (from the
  transcript) and export title text (from the pipeline), so neither can inject
  libass render directives.
- Escape single quotes and backslashes in concat-demuxer clip paths.
- Bound `export` `resolution`, `fps` and `title_size`, plus `speed` `factor`,
  to sane ranges to avoid absurd FFmpeg allocations.
- Reject empty or dotted `--var` keys instead of silently dropping them.

## [0.1.4]

### Added

- CLI commands: `init`, `validate` and `run`.
- YAML validation for the v1 pipeline format.
- Local execution engine with DAG ordering, channels, matrix runs, caching and failure handling.
- Built-in blocks for STT, clip detection, cutting, captions, export and concat.
- Local Whisper integration through `faster-whisper`.
- Dockerfile, GitHub Actions workflows, tests and quality checks.

### Notes

- MP4 input is the supported input target for the current engine.
- The media engine dependencies are installed through the optional `engine` extra.
