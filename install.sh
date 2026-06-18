#!/usr/bin/env bash
#
# Reelflow — installation sur distributions Linux basées Debian (apt) :
# Debian, Ubuntu, Lubuntu, Linux Mint, Pop!_OS, etc.
# À lancer depuis la racine du dépôt :  ./install.sh
#
# Installe les prérequis système, crée un venv et installe Reelflow + son moteur.
# FFmpeg n'est PAS requis au niveau système : il est embarqué via imageio-ffmpeg.

set -euo pipefail

echo "▶ Reelflow — installation (distributions Debian/apt)"

# --- 1. Dépendances système ------------------------------------------------
# python3 + venv + pip : pour l'environnement et l'installation
# git                  : pour récupérer/maj le projet
# fontconfig           : pour que libass (titres/sous-titres) résolve les polices
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git fontconfig
# Optionnel : utiliser le FFmpeg système plutôt que celui embarqué
#   sudo apt-get install -y ffmpeg

# --- 2. Environnement Python -----------------------------------------------
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip

# "[engine]" = moteur média (FFmpeg embarqué + faster-whisper).
# Sans extra, seul `reelflow validate` fonctionne.
pip install -e ".[engine]"

# --- 3. Vérification --------------------------------------------------------
reelflow --version
echo
echo "✓ Reelflow installé."
echo "  Active l'environnement :  source .venv/bin/activate"
echo "  Démarre :                 reelflow init pipeline.yaml"
echo "                            reelflow validate pipeline.yaml"
echo "                            reelflow run pipeline.yaml"
echo
echo "ℹ Au 1er run, le modèle Whisper (~140 Mo) et les polices se téléchargent (puis en cache)."
