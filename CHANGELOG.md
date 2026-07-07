# Changelog

All notable changes to LeMontage are tracked here.

This project follows SemVer-style versioning while it is pre-1.0: minor versions
may still introduce breaking changes, and those changes must be called out here.

## [Unreleased]

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
