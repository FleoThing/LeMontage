"""Tests for the Reelflow CLI."""

import yaml

from reelflow.cli import STARTER_PIPELINE, main
from reelflow.validator import validate_doc


def test_starter_pipeline_is_valid():
    assert validate_doc(yaml.safe_load(STARTER_PIPELINE)) == []


def test_init_writes_valid_pipeline(tmp_path):
    target = tmp_path / "pipeline.yaml"
    assert main(["init", str(target)]) == 0
    assert target.exists()
    assert validate_doc(yaml.safe_load(target.read_text())) == []


def test_init_refuses_to_overwrite(tmp_path):
    target = tmp_path / "pipeline.yaml"
    target.write_text("existing", encoding="utf-8")
    assert main(["init", str(target)]) == 1
    assert target.read_text() == "existing"


def test_init_force_overwrites(tmp_path):
    target = tmp_path / "pipeline.yaml"
    target.write_text("existing", encoding="utf-8")
    assert main(["init", str(target), "--force"]) == 0
    assert "reelflow" in target.read_text()


def test_validate_ok(tmp_path):
    target = tmp_path / "p.yaml"
    target.write_text(STARTER_PIPELINE, encoding="utf-8")
    assert main(["validate", str(target)]) == 0


def test_validate_reports_errors(tmp_path, capsys):
    target = tmp_path / "p.yaml"
    target.write_text("name: broken\n", encoding="utf-8")
    assert main(["validate", str(target)]) == 1
    assert "error" in capsys.readouterr().err


def test_run_on_valid_is_wip(tmp_path):
    """run validates, then reports the engine is not implemented yet (exit 2)."""
    target = tmp_path / "p.yaml"
    target.write_text(STARTER_PIPELINE, encoding="utf-8")
    assert main(["run", str(target)]) == 2


def test_run_on_invalid_fails_validation(tmp_path):
    target = tmp_path / "p.yaml"
    target.write_text("name: broken\n", encoding="utf-8")
    assert main(["run", str(target)]) == 1
