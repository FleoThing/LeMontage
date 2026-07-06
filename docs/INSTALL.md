# Install And Deployment Guide

The README contains the short install path. This document gives the same options
with operational notes.

## Recommended Paths

| Need | Method |
|---|---|
| Daily CLI usage | `pipx` from a reviewed Git ref |
| Fast local tryout | one-line installer |
| Isolated local runtime | Docker Compose |
| CI or server build | Docker CLI |
| Development | source install |

## `pipx`

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install "lemontage[engine] @ git+https://github.com/FleoThing/LeMontage@main"
lemontage --version
```

Use a tag or commit SHA instead of `main` when reproducibility matters.

## One-Line Installer

```bash
curl -fsSL https://raw.githubusercontent.com/FleoThing/LeMontage/main/infrastructure/script/get.sh | bash
```

Safer variant:

```bash
curl -fsSL https://raw.githubusercontent.com/FleoThing/LeMontage/main/infrastructure/script/get.sh -o get.sh
less get.sh
bash get.sh
```

## Docker Compose

```bash
git clone https://github.com/FleoThing/LeMontage
cd LeMontage
docker compose -f infrastructure/local/compose.yaml build
docker compose -f infrastructure/local/compose.yaml run --rm lemontage --help
```

Run a pipeline:

```bash
docker compose -f infrastructure/local/compose.yaml run --rm lemontage run pipeline.yaml
```

Compose mounts the repository at `/work` and stores caches in named Docker volumes.

## Docker CLI

```bash
git clone https://github.com/FleoThing/LeMontage
cd LeMontage
docker build -t lemontage .
docker run --rm -v "$PWD":/work lemontage --help
```

Keep caches between runs:

```bash
docker run --rm -v "$PWD":/work \
  -v lemontage-cache:/root/.lemontage \
  -v hf-cache:/root/.cache/huggingface \
  lemontage run pipeline.yaml
```

## Source Install

```bash
git clone https://github.com/FleoThing/LeMontage
cd LeMontage
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
pip install -e ".[engine,dev]"
```

## Install Scripts

```bash
git clone https://github.com/FleoThing/LeMontage
cd LeMontage
./infrastructure/script/install.sh
```

Windows PowerShell:

```powershell
.\infrastructure\script\install.ps1
```

Review scripts before running them on shared or production machines.
