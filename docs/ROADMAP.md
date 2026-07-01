# Roadmap

This roadmap is a planning document. It is not a compatibility promise.

## v0.2.0

Goal: make LeMontage easier to install, deploy and use for real creator workflows.

Expected work:

- Package and release hardening.
- Better Docker and Compose workflows for local runs.
- More production-ready examples under `examples/`.
- Export presets and transition primitives.
- Stronger validation messages for common YAML mistakes.
- Documentation cleanup around install, security and the spec.

Definition of done:

- A new user can install and run an example without reading source code.
- CI covers changed behavior.
- Docs and examples match the current CLI and YAML spec.

## v0.3.0

Goal: prepare the ecosystem layer while keeping the core engine local-first.

Expected work:

- Community pipeline metadata and contribution rules.
- Optional provider interfaces for cloud STT, TTS and LLM services.
- Local TTS exploration behind optional extras.
- Better run observability: structured logs, summaries and cache reporting.
- More robust long-video workflows.

Definition of done:

- Provider additions remain optional and do not make the core install heavy.
- Pipeline files stay portable and reviewable.
- New features have examples, spec updates and focused tests.
