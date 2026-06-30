"""Title fonts: preset families bundled locally, used by libass via ``fontsdir``.

Presets ``font1``…``font5`` render identically on any machine — no system font
install — because the (OFL-licensed) font is downloaded once to
``~/.lemontage/fonts/`` and libass is pointed at that directory. Users can also
drop their own ``.ttf`` there and reference it by family name.

``family()`` is pure (alias → family name); ``ensure()`` does the network fetch
and is only called at render time.
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
from pathlib import Path

_GF = "https://github.com/google/fonts/raw/main/ofl"

# alias -> (family name as libass resolves it, download URL). All static OFL TTFs.
PRESETS: dict[str, tuple[str, str]] = {
    "font1": ("Anton", f"{_GF}/anton/Anton-Regular.ttf"),
    "font2": ("Bebas Neue", f"{_GF}/bebasneue/BebasNeue-Regular.ttf"),
    "font3": ("Bangers", f"{_GF}/bangers/Bangers-Regular.ttf"),
    "font4": ("Archivo Black", f"{_GF}/archivoblack/ArchivoBlack-Regular.ttf"),
    "font5": ("Fjalla One", f"{_GF}/fjallaone/FjallaOne-Regular.ttf"),
}
_DEFAULT_ALIAS = "font1"


def fonts_dir() -> Path:
    """Local title-font directory (absolute), honouring ``LEMONTAGE_HOME``."""
    home = os.environ.get("LEMONTAGE_HOME")
    base = Path(home) if home else Path.home() / ".lemontage"
    path = (base / "fonts").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _escape_filter_path(path: Path) -> str:
    """Absolutise and escape a path for use inside an FFmpeg filtergraph.

    libass resolves ``ass=`` and ``fontsdir=`` relative to FFmpeg's working
    directory, so a relative path silently fails when the process is launched
    elsewhere — we resolve to an absolute path first. The backslash/colon
    escaping is what the filtergraph parser needs on Windows (``C:\\…``); on
    POSIX paths it is a harmless no-op.
    """
    text = str(path.resolve())
    return text.replace("\\", "\\\\").replace(":", "\\:")


def libass_filter(ass: Path) -> str:
    """Build an ``ass=…:fontsdir=…`` video filter with absolute, escaped paths."""
    return f"ass='{_escape_filter_path(ass)}':fontsdir='{_escape_filter_path(fonts_dir())}'"


def family(title_font: object) -> str:
    """Resolve a ``font1``…``font5`` alias to a family; pass any other name through."""
    if not title_font:
        return PRESETS[_DEFAULT_ALIAS][0]
    key = str(title_font).lower()
    if key in PRESETS:
        return PRESETS[key][0]
    return str(title_font)


def ensure(title_font: object) -> None:
    """Fetch the preset font if needed; warn when a custom font is unavailable."""
    key = str(title_font).lower() if title_font else _DEFAULT_ALIAS
    if key in PRESETS:
        _download(PRESETS[key][1])
        return
    fam = str(title_font)
    if not _available(fam):
        _warn(
            f"police '{fam}' introuvable (ni installée, ni preset font1-5) — elle sera "
            f"substituée. Installe-la ou dépose un .ttf dans {fonts_dir()}"
        )


# A real font file starts with one of these signatures. Guards against an
# error page (HTML/JSON) being silently saved with a .ttf name and "installed".
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1", b"ttcf", b"wOFF", b"wOF2")


def _download(url: str) -> bool:
    target = fonts_dir() / url.rsplit("/", 1)[-1]
    if target.exists() and target.stat().st_size > 0:
        return True
    try:
        # A User-Agent avoids GitHub's 403 for header-less clients; the timeout
        # keeps a hung connection from blocking the whole render.
        req = urllib.request.Request(url, headers={"User-Agent": "lemontage"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - trusted Google Fonts URL
            data = resp.read()
        if not data.startswith(_FONT_MAGIC):
            raise OSError("server did not return a font file")
        # `export` maps over clips in parallel, so several threads can fetch the
        # same font at once. Write to a UNIQUE temp file (not a shared
        # "<name>.part") and atomically replace, so the writers never race on
        # one path — a shared .part made all-but-one rename fail with ENOENT.
        fd, tmp_name = tempfile.mkstemp(dir=fonts_dir(), suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)  # atomic; last writer wins, no ENOENT
        finally:
            tmp.unlink(missing_ok=True)  # no-op once renamed away
        return True
    except OSError as exc:
        # A peer thread may have just installed it — that's success, not failure.
        if target.exists() and target.stat().st_size > 0:
            return True
        _warn(f"téléchargement de la police {target.name} échoué ({exc}) — substituée")
        return False


def _available(fam: str) -> bool:
    """Best-effort check that a custom family will actually be used (not substituted)."""
    needle = fam.lower().replace(" ", "")
    for ttf in fonts_dir().glob("*.ttf"):
        if needle in ttf.stem.lower().replace(" ", ""):
            return True
    import shutil
    import subprocess

    fc = shutil.which("fc-match")
    if not fc:
        return True  # can't verify -> don't cry wolf
    try:
        out = subprocess.run(
            [fc, "-f", "%{family}", fam], capture_output=True, text=True, timeout=5
        )
        return fam.lower() in out.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return True


def _warn(message: str) -> None:
    print(f"⚠ lemontage: {message}", file=sys.stderr)
