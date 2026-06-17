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


def test_run_on_invalid_fails_validation(tmp_path):
    target = tmp_path / "p.yaml"
    target.write_text("name: broken\n", encoding="utf-8")
    assert main(["run", str(target)]) == 1


def test_run_surfaces_engine_errors(tmp_path):
    """A valid pipeline whose input media is missing fails cleanly (exit 1)."""
    missing = tmp_path / "does-not-exist.mp4"
    pipeline = (
        'reelflow: "1.0"\n'
        "name: t\n"
        f"input:\n  type: video\n  source: {missing}\n"
        "steps:\n  - id: clips\n    detect_clips:\n      emit: ch\n"
        f"output:\n  dir: {tmp_path}\n"
    )
    target = tmp_path / "p.yaml"
    target.write_text(pipeline, encoding="utf-8")
    assert main(["run", str(target)]) == 1


def test_run_executes_pipeline(tmp_path, monkeypatch):
    """A valid pipeline with stubbed blocks runs to success (exit 0)."""
    from reelflow.engine import executor
    from reelflow.engine.blocks.base import Block, BlockResult

    class NoopBlock(Block):
        def __init__(self, name):
            self.name = name

        def execute(self, params, ctx, step_id):
            return BlockResult(outputs={})

    registry = {name: NoopBlock(name) for name in ("stt", "detect_clips", "cut", "captions")}
    monkeypatch.setattr(executor, "REGISTRY", registry)

    pipeline = (
        'reelflow: "1.0"\n'
        "name: t\n"
        "input:\n  type: video\n  source: ./x.mp4\n"
        "steps:\n  - id: a\n    stt: {}\n"
        "output:\n  dir: " + str(tmp_path) + "\n"
    )
    target = tmp_path / "p.yaml"
    target.write_text(pipeline, encoding="utf-8")
    assert main(["run", str(target), "--var", "k=v"]) == 0
