# Contributing

LeMontage is early-stage. Contributions should keep the project local-first,
declarative and easy to validate.

## Development Setup

```bash
git clone https://github.com/FleoThing/LeMontage
cd LeMontage

python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
pip install -e ".[engine,dev]"
```

Install Git hooks:

```bash
pre-commit install
pre-commit install --hook-type pre-push
```

Hadolint is used by the Dockerfile hook. Install it locally if you want the full
pre-commit result before pushing.

## Local Checks

Run the same checks expected by CI:

```bash
ruff check src tests
ruff format --check src tests
pytest -q
docker build -t lemontage:local .
docker compose -f infrastructure/local/compose.yaml config
```

Run every pre-commit hook manually:

```bash
pre-commit run --all-files
```

## Pipeline Usage

Create and validate a starter pipeline:

```bash
lemontage init pipeline.yaml
lemontage validate pipeline.yaml
```

Validate an example pipeline:

```bash
lemontage validate examples/podcast-to-clips.yaml
```

Run a pipeline after setting `input.source` to a real local video:

```bash
lemontage run pipeline.yaml --var lang=fr --clean
```

## Pull Request Expectations

- Keep changes focused and scoped to one behavior or documentation topic.
- Add or update tests when changing validation, DAG behavior, executor behavior or blocks.
- Update `docs/SPEC.md` when changing YAML syntax or block contracts.
- Update examples when adding user-facing pipeline behavior.
- Run `pre-commit run --all-files` and `pytest -q` before opening a PR.

## CI Pipeline

The GitHub Actions pipeline currently covers:

- Tests on Ubuntu, macOS and Windows for Python 3.10, 3.11 and 3.12.
- Ruff lint and format checks.
- Hadolint against the Dockerfile.
- Docker image build.
- Trivy filesystem scan for high and critical vulnerabilities.
- CodeQL analysis for Python and GitHub Actions.
- Release image publishing on SemVer tags.

## Roadmap Boilerplate

### v0.2.0

Goal: make LeMontage easier to install, deploy and use for real creator workflows.

Expected work:

- Package and release hardening: PyPI readiness, GitHub releases and GHCR images.
- Better Docker and Compose workflows for local runs.
- More production-ready examples under `examples/`.
- Export presets and transition primitives.
- Stronger validation messages for common YAML mistakes.
- Documentation cleanup around install, security and the spec.

Definition of done:

- A new user can install and run an example without reading source code.
- CI covers changed behavior.
- Docs and examples match the current CLI and YAML spec.

### v0.3.0

Goal: prepare the ecosystem layer while keeping the core engine local-first.

Expected work:

- Community pipeline hub foundations: schema, metadata and contribution rules.
- Optional provider interfaces for cloud STT, TTS and LLM services.
- Local TTS exploration behind optional extras.
- Better run observability: structured logs, summaries and cache reporting.
- More robust long-video workflows.

Definition of done:

- Provider additions remain optional and do not make the core install heavy.
- Pipeline files stay portable and reviewable.
- New features have examples, spec updates and focused tests.
