"""Tests for title-font resolution (no network — only the pure parts)."""

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
    assert Path(inner).is_absolute()
    assert inner.endswith("clip.ass")
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
    assert fonts._download("https://example.test/Anton-Regular.ttf") is True
    saved = fonts.fonts_dir() / "Anton-Regular.ttf"
    assert saved.read_bytes().startswith(b"\x00\x01\x00\x00")
