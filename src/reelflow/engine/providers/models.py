"""Download and cache local model files under ``~/.reelflow/models/``.

Whisper manages its own cache; this helper covers files we must fetch directly
(the kokoro ONNX weights and voice pack).
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

KOKORO_FILES = {
    "kokoro-v1.0.onnx": f"{_RELEASE}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{_RELEASE}/voices-v1.0.bin",
}


def models_dir() -> Path:
    """Return the model cache directory, honouring ``REELFLOW_HOME``."""
    home = os.environ.get("REELFLOW_HOME")
    base = Path(home) if home else Path.home() / ".reelflow"
    path = base / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_file(name: str, url: str) -> Path:
    """Download ``url`` to the cache as ``name`` if not already present."""
    target = models_dir() / name
    if target.exists() and target.stat().st_size > 0:
        return target
    tmp = target.with_suffix(target.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 - trusted release URL
    tmp.replace(target)
    return target


def ensure_kokoro() -> tuple[Path, Path]:
    """Ensure the kokoro model + voices are cached; return their paths."""
    model = ensure_file("kokoro-v1.0.onnx", KOKORO_FILES["kokoro-v1.0.onnx"])
    voices = ensure_file("voices-v1.0.bin", KOKORO_FILES["voices-v1.0.bin"])
    return model, voices
