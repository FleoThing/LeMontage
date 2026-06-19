#!/usr/bin/env bash
#
# Reelflow one-line installer (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/ffillouxdev/reelflow/dev/get.sh | bash
#
# Installs pipx if needed, then installs Reelflow (with its media engine) as a
# global CLI you can run from anywhere. Works on Linux and macOS.

set -euo pipefail

SPEC="reelflow[engine] @ git+https://github.com/ffillouxdev/reelflow@dev"

echo "▶ Installing Reelflow…"

# 1. Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required. Install it and re-run." >&2
  exit 1
fi

# 2. Ensure pipx (and fontconfig for caption/title fonts) via the available pkg mgr
if ! command -v pipx >/dev/null 2>&1; then
  echo "  · Installing pipx…"
  if command -v brew >/dev/null 2>&1; then
    brew install pipx fontconfig
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y pipx fontconfig
  else
    python3 -m pip install --user pipx
  fi
  python3 -m pipx ensurepath || true
  export PATH="$HOME/.local/bin:$PATH"
fi

# 3. Install Reelflow
pipx install "$SPEC"

echo
echo "✓ Reelflow installed."
echo "  Open a new terminal (so the PATH update takes effect), then:"
echo "    reelflow init pipeline.yaml && reelflow run pipeline.yaml"
echo
echo "ℹ On first run the Whisper model (~140 MB) and fonts download, then cache."
