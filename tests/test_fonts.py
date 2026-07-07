"""Tests for title-font resolution (no network — only the pure parts)."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lemontage.engine import fonts


class _FakeResp:
    """Minimal context-manager stand-in for urllib's urlopen response."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_family_default_is_a_preset():
    assert fonts.family(None) == "Anton"  # font1


def test_family_alias_resolves():
    assert fonts.family("font2") == "Bebas Neue"
    assert fonts.family("FONT3") == "Bangers"  # case-insensitive


def test_family_literal_passes_through():
    assert fonts.family("Impact") == "Impact"
    assert fonts.family("Helvetica Neue") == "Helvetica Neue"


def test_fonts_dir_honours_lemontage_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    assert fonts.fonts_dir() == tmp_path / "fonts"
    assert fonts.fonts_dir().is_dir()


def test_warns_on_missing_custom_font(monkeypatch, capsys):
    monkeypatch.setattr(fonts, "_available", lambda _fam: False)
    fonts.ensure("Totally Missing Font XYZ")
    assert "introuvable" in capsys.readouterr().err


def test_no_warning_for_available_custom_font(monkeypatch, capsys):
    monkeypatch.setattr(fonts, "_available", lambda _fam: True)
    fonts.ensure("Impact")
    assert capsys.readouterr().err == ""


def test_fonts_dir_is_absolute(tmp_path, monkeypatch):
    # Even a relative LEMONTAGE_HOME resolves to an absolute fonts directory.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LEMONTAGE_HOME", "rel-home")
    assert fonts.fonts_dir().is_absolute()


def test_libass_filter_uses_absolute_ass_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    flt = fonts.libass_filter(Path("relative/clip.ass"))
    inner = flt.split("ass='", 1)[1].split("'", 1)[0]
    # libass_filter escapes the path for the filtergraph (\\ and \:); undo that
    # before checking, otherwise a Windows drive path "C:\..." -> "C\:\\..." is no
    # longer recognised as absolute. On POSIX the un-escaping is a no-op.
    unescaped = inner.replace("\\:", ":").replace("\\\\", "\\")
    assert unescaped == str(Path("relative/clip.ass").resolve())
    assert Path(unescaped).is_absolute()
    assert "fontsdir='" in flt


def test_download_rejects_non_font_payload(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    monkeypatch.setattr(
        fonts.urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(b"<!DOCTYPE html>404")
    )
    assert fonts._download("https://example.test/Anton-Regular.ttf") is False
    assert "échoué" in capsys.readouterr().err
    assert not (fonts.fonts_dir() / "Anton-Regular.ttf").exists()
    # The partial download is cleaned up, not left behind.
    assert not (fonts.fonts_dir() / "Anton-Regular.ttf.part").exists()


def test_download_accepts_real_font(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    monkeypatch.setattr(
        fonts.urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(b"\x00\x01\x00\x00FONTDATA")
    )
    # A non-preset name has no pinned digest: magic bytes are the only gate.
    assert fonts._download("https://example.test/Custom-Regular.ttf") is True
    saved = fonts.fonts_dir() / "Custom-Regular.ttf"
    assert saved.read_bytes().startswith(b"\x00\x01\x00\x00")


def test_download_rejects_wrong_checksum(tmp_path, monkeypatch, capsys):
    # Right magic bytes, wrong SHA-256: a substituted preset font is refused
    # (S6 — MITM or compromised upstream must not reach libass).
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    monkeypatch.setattr(
        fonts.urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(b"\x00\x01\x00\x00EVILFONT")
    )
    assert fonts._download("https://example.test/Anton-Regular.ttf") is False
    assert "échoué" in capsys.readouterr().err
    assert not (fonts.fonts_dir() / "Anton-Regular.ttf").exists()
    assert not list(fonts.fonts_dir().glob("*.part"))


def test_download_accepts_matching_checksum(tmp_path, monkeypatch):
    import hashlib

    data = b"\x00\x01\x00\x00FONTDATA"
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    monkeypatch.setattr(fonts.urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(data))
    monkeypatch.setitem(fonts._FONT_SHA256, "Anton-Regular.ttf", hashlib.sha256(data).hexdigest())
    assert fonts._download("https://example.test/Anton-Regular.ttf") is True
    assert (fonts.fonts_dir() / "Anton-Regular.ttf").read_bytes() == data


def test_all_presets_have_pinned_checksums():
    preset_files = {url.rsplit("/", 1)[-1] for _fam, url in fonts.PRESETS.values()}
    assert preset_files == set(fonts._FONT_SHA256)


def test_download_is_concurrency_safe(tmp_path, monkeypatch, capsys):
    # `export` maps over clips in parallel, so several threads fetch the same
    # font at once. They must not race on a shared temp path — a shared
    # "<name>.part" made all-but-one rename fail with ENOENT and emit a spurious
    # "téléchargement échoué" warning.
    monkeypatch.setenv("LEMONTAGE_HOME", str(tmp_path))
    monkeypatch.setattr(
        fonts.urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(b"\x00\x01\x00\x00FONTDATA")
    )
    url = "https://example.test/Custom-Regular.ttf"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: fonts._download(url), range(16)))

    assert all(results)  # every concurrent caller succeeds
    assert capsys.readouterr().err == ""  # no spurious failure warning
    saved = fonts.fonts_dir() / "Custom-Regular.ttf"
    assert saved.read_bytes().startswith(b"\x00\x01\x00\x00")
    # No stray temp files left behind.
    assert not list(fonts.fonts_dir().glob("*.part"))
