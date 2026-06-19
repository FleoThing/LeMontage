"""Tests for title-font resolution (no network — only the pure parts)."""

from reelflow.engine import fonts


def test_family_default_is_a_preset():
    assert fonts.family(None) == "Anton"  # font1


def test_family_alias_resolves():
    assert fonts.family("font2") == "Bebas Neue"
    assert fonts.family("FONT3") == "Bangers"  # case-insensitive


def test_family_literal_passes_through():
    assert fonts.family("Impact") == "Impact"
    assert fonts.family("Helvetica Neue") == "Helvetica Neue"


def test_fonts_dir_honours_reelflow_home(tmp_path, monkeypatch):
    monkeypatch.setenv("REELFLOW_HOME", str(tmp_path))
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
