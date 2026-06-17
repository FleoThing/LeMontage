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


def _noop_pipeline(tmp_path):
    """A valid pipeline with one stubbed step writing under tmp_path."""
    from reelflow.engine.blocks.base import Block, BlockResult

    class NoopBlock(Block):
        def __init__(self, name):
            self.name = name

        def execute(self, params, ctx, step_id):
            return BlockResult(outputs={})

    pipeline = (
        'reelflow: "1.0"\nname: t\n'
        "input:\n  type: video\n  source: ./x.mp4\n"
        "steps:\n  - id: a\n    stt: {}\n"
        "output:\n  dir: " + str(tmp_path) + "\n"
    )
    target = tmp_path / "p.yaml"
    target.write_text(pipeline, encoding="utf-8")
    return target, NoopBlock


def test_run_clean_removes_temp_dir(tmp_path, monkeypatch):
    from reelflow.engine import executor

    target, NoopBlock = _noop_pipeline(tmp_path)
    monkeypatch.setattr(executor, "REGISTRY", {"stt": NoopBlock("stt")})
    assert main(["run", str(target), "--clean"]) == 0
    assert not (tmp_path / ".reelflow").exists()


def test_run_without_clean_keeps_temp_dir(tmp_path, monkeypatch):
    from reelflow.engine import executor

    target, NoopBlock = _noop_pipeline(tmp_path)
    monkeypatch.setattr(executor, "REGISTRY", {"stt": NoopBlock("stt")})
    assert main(["run", str(target)]) == 0
    assert (tmp_path / ".reelflow").exists()  # cache/checkpoints kept for resume


def test_clean_removes_concat_parts_keeps_reel(tmp_path, monkeypatch):
    """--clean also deletes per-clip files a concat merged, keeping the reel."""
    from reelflow.engine import executor
    from reelflow.engine.blocks.base import Block, BlockResult

    parts = [tmp_path / "p0.mp4", tmp_path / "p1.mp4"]
    reel = tmp_path / "reel.mp4"
    for f in (*parts, reel):
        f.write_bytes(b"v")

    class FakeConcat(Block):
        name = "concat"

        def execute(self, params, ctx, step_id):
            return BlockResult(outputs={"file": str(reel), "parts": [str(p) for p in parts]})

    monkeypatch.setattr(executor, "REGISTRY", {"concat": FakeConcat()})
    pipeline = (
        'reelflow: "1.0"\nname: t\n'
        "input:\n  type: video\n  source: ./x.mp4\n"
        "steps:\n  - id: reel\n    concat: {}\n"
        f"output:\n  dir: {tmp_path}\n"
    )
    target = tmp_path / "p.yaml"
    target.write_text(pipeline, encoding="utf-8")
    assert main(["run", str(target), "--clean"]) == 0
    assert not parts[0].exists() and not parts[1].exists()  # parts removed
    assert reel.exists()  # final reel kept


def test_output_cleanup_flag_in_yaml_removes_temp(tmp_path, monkeypatch):
    """`output.cleanup: true` triggers cleanup without the CLI flag."""
    from reelflow.engine import executor
    from reelflow.engine.blocks.base import Block, BlockResult

    class NoopBlock(Block):
        def __init__(self, name):
            self.name = name

        def execute(self, params, ctx, step_id):
            return BlockResult(outputs={})

    pipeline = (
        'reelflow: "1.0"\nname: t\n'
        "input:\n  type: video\n  source: ./x.mp4\n"
        "steps:\n  - id: a\n    stt: {}\n"
        f"output:\n  dir: {tmp_path}\n  cleanup: true\n"
    )
    target = tmp_path / "p.yaml"
    target.write_text(pipeline, encoding="utf-8")
    monkeypatch.setattr(executor, "REGISTRY", {"stt": NoopBlock("stt")})
    assert main(["run", str(target)]) == 0  # no --clean flag
    assert not (tmp_path / ".reelflow").exists()
