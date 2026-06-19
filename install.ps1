# LeMontage - install from source on Windows (PowerShell).
# Run from the repo root:  ./install.ps1
#
# Creates a venv and installs LeMontage + its engine. FFmpeg is bundled via
# imageio-ffmpeg, so no system ffmpeg is required.

$ErrorActionPreference = "Stop"

Write-Host "LeMontage - installation (from source, Windows)"

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
# Without the extra, only `lemontage validate` works.
pip install -e ".[engine]"

# --- 3. Done ---------------------------------------------------------------
lemontage --version
Write-Host ""
Write-Host "LeMontage installed."
Write-Host "  Activate the env:  .\.venv\Scripts\Activate.ps1"
Write-Host "  Get started:       lemontage init pipeline.yaml"
Write-Host "                     lemontage run pipeline.yaml"
Write-Host ""
Write-Host "On first run the Whisper model (~140 MB) and fonts download, then cache."
Write-Host "(Windows has no 'man'; see docs/lemontage.1 or the README for the CLI reference.)"
