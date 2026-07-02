#!/usr/bin/env bash
#
# LeMontage one-line installer (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/FleoThing/LeMontage/main/infrastructure/script/get.sh | bash
#
# Installs pipx if needed, then installs LeMontage (with its media engine) as a
# global CLI you can run from anywhere. Works on Linux and macOS.

set -euo pipefail

SPEC="lemontage[engine] @ git+https://github.com/FleoThing/LeMontage@main"

echo "▶ Installing LeMontage…"

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

# 3. Install LeMontage
pipx install "$SPEC"

# 4. Install the man page so `man lemontage` works (best-effort, never fatal).
#    pipx installs into an isolated venv that is not on any MANPATH, so we drop
#    the page into the user manpath and refresh the index when man-db is present.
MAN_DIR="$HOME/.local/share/man/man1"
MAN_URL="https://raw.githubusercontent.com/FleoThing/LeMontage/main/docs/lemontage.1"
if command -v curl >/dev/null 2>&1; then
  mkdir -p "$MAN_DIR"
  if curl -fsSL "$MAN_URL" -o "$MAN_DIR/lemontage.1"; then
    if command -v mandb >/dev/null 2>&1; then
      mandb -q "$HOME/.local/share/man" >/dev/null 2>&1 || true
    fi
    echo "  · Installed man page → run 'man lemontage'"
  fi
fi

echo
echo "✓ LeMontage installed."
echo "  Open a new terminal (so the PATH update takes effect), then:"
echo "    lemontage init pipeline.yaml && lemontage run pipeline.yaml"
echo
echo "ℹ On first run the Whisper model (~140 MB) and fonts download, then cache."
