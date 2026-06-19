#!/usr/bin/env bash
#
# Reelflow - install from source on Linux (apt) or macOS (Homebrew).
# Run from the repo root:  ./install.sh
#
# Installs the system prerequisites, creates a venv, installs Reelflow + its
# engine, and installs the man page. FFmpeg is bundled via imageio-ffmpeg, so no
# system ffmpeg is required.  (Windows: use install.ps1.)

set -euo pipefail

echo "▶ Reelflow - installation (from source)"

# --- 1. System prerequisites -----------------------------------------------
# python3 + venv + pip : environment and install
# git                  : fetch/update the project
# fontconfig           : let libass (titles/captions) resolve fonts
case "$(uname -s)" in
  Linux)
    echo "  · Linux detected - installing prerequisites via apt"
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip git fontconfig
    ;;
  Darwin)
    echo "  · macOS detected - installing prerequisites via Homebrew"
    command -v brew >/dev/null 2>&1 || {
      echo "Homebrew is required (https://brew.sh)" >&2
      exit 1
    }
    brew install python git fontconfig
    ;;
  *)
    echo "Unsupported OS. Install Python 3.10+ then: pip install -e \".[engine]\"" >&2
    exit 1
    ;;
esac

# --- 2. Python environment -------------------------------------------------
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip

# "[engine]" = the media engine (bundled FFmpeg + faster-whisper).
# Without the extra, only `reelflow validate` works.
pip install -e ".[engine]"

# --- 3. Man page (best-effort) ---------------------------------------------
man_dir="$HOME/.local/share/man/man1"
mkdir -p "$man_dir"
cp docs/reelflow.1 "$man_dir/"
if command -v mandb >/dev/null 2>&1; then
  mandb -q "$HOME/.local/share/man" 2>/dev/null || true
fi
echo "  · man page installed (try: man reelflow)"

# --- 4. Done ---------------------------------------------------------------
reelflow --version
echo
echo "✓ Reelflow installed."
echo "  Activate the env:  source .venv/bin/activate"
echo "  Get started:       reelflow init pipeline.yaml"
echo "                     reelflow validate pipeline.yaml"
echo "                     reelflow run pipeline.yaml"
echo
echo "ℹ On first run the Whisper model (~140 MB) and fonts download, then cache."
