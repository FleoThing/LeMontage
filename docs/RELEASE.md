# Release Process

This document describes the intended release checklist for maintainers.

## Versioning

LeMontage is pre-1.0. Use SemVer-style tags:

```text
vMAJOR.MINOR.PATCH
```

Examples:

```text
v0.1.4
v0.2.0
```

## Pre-Release Checklist

1. Update the version in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Run local quality checks:

```bash
make check          # ruff check + ruff format --check + pytest -q
make audit          # advisory static analysis + block/doc consistency
docker compose -f infrastructure/local/compose.yaml config
docker build -t lemontage:release-check .
```

`make audit` is advisory except for its last section: `scripts/audit_blocks.py`
exits non-zero when a block is registered without a SPEC section, missing from
the man page, or when the SPEC §6 numbering has drifted. Fix those before
tagging; the ruff sections above them are noisy by design (see the Makefile).

4. Verify install docs in `README.md` and `docs/INSTALL.md`.
5. Check-up `man` docs and the windows docs
6. Commit the release changes.
7. Create and push a SemVer tag.

```bash
git tag v0.1.4
git push origin v0.1.4
```

## GitHub Actions

The release workflow runs on tags matching `v*`. It validates SemVer, builds the
Docker image and creates a GitHub release.

## After Release

1. Confirm the GitHub release exists.
2. Confirm release notes are generated.
3. Smoke test install or Docker usage from the published ref.
4. Open the next `CHANGELOG.md` `[Unreleased]` section if needed.
