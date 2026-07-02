# Changelog

All notable changes to LeMontage are tracked here.

This project follows SemVer-style versioning while it is pre-1.0: minor versions
may still introduce breaking changes, and those changes must be called out here.

## [Unreleased]

### Added

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

## [0.1.4] - Current

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
