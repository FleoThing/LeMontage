# Security Policy

## Supported Versions

LeMontage is currently pre-1.0. Security fixes are handled on the latest codebase
and released through the next available version.

| Version | Supported |
|---|---|
| latest `main` | Yes |
| older pre-1.0 versions | Best effort |

## Reporting A Vulnerability

Please do not open a public issue for a suspected vulnerability.

Report privately through GitHub Security Advisories if available on the repository.
If advisories are not available, contact the maintainer through the GitHub profile
linked from the repository owner.

Include:

- A short description of the issue.
- Steps to reproduce.
- Affected version, commit or branch.
- Impact and any known workaround.
- Whether the issue is already public.

## Scope

Security-sensitive areas include:

- Pipeline YAML parsing and validation.
- File path handling for input, output and intermediate artifacts.
- Docker images and deployment scripts.
- Dependency updates and supply-chain issues.
- Handling of untrusted media files.
- Shell installation scripts under `infrastructure/script/`.

## Install Safety

For the safest install path, clone the repository and inspect scripts before
running them. Docker Compose is preferred when you want runtime isolation.

Avoid `curl | bash` on production or shared machines unless you have reviewed the
script and pinned the exact commit or tag you trust.

## Dependency And CI Checks

The repository uses:

- Trivy for filesystem vulnerability scanning.
- CodeQL for static analysis.
- Hadolint for Dockerfile checks.
- Ruff and pre-commit for Python quality gates.

These checks reduce risk but do not replace review, pinned dependencies and careful
handling of untrusted media.
