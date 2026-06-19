# Reelflow - install from source on Windows (PowerShell).
# Run from the repo root:  ./install.ps1
#
# Creates a venv and installs Reelflow + its engine. FFmpeg is bundled via
# imageio-ffmpeg, so no system ffmpeg is required.

$ErrorActionPreference = "Stop"

Write-Host "Reelflow - installation (from source, Windows)"

# --- 1. Python -------------------------------------------------------------
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
else {
    Write-Error "Python 3.10+ is required. Install it:  winget install Python.Python.3.12"
}

# --- 2. Python environment -------------------------------------------------
& $py -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# "[engine]" = the media engine (bundled FFmpeg + faster-whisper).
# Without the extra, only `reelflow validate` works.
pip install -e ".[engine]"

# --- 3. Done ---------------------------------------------------------------
reelflow --version
Write-Host ""
Write-Host "Reelflow installed."
Write-Host "  Activate the env:  .\.venv\Scripts\Activate.ps1"
Write-Host "  Get started:       reelflow init pipeline.yaml"
Write-Host "                     reelflow run pipeline.yaml"
Write-Host ""
Write-Host "On first run the Whisper model (~140 MB) and fonts download, then cache."
Write-Host "(Windows has no 'man'; see docs/reelflow.1 or the README for the CLI reference.)"
