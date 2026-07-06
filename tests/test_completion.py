"""Tests for shell-completion script generation."""

import pytest

from lemontage.cli import build_parser, main
from lemontage.completion import completion_script


def script(shell):
    return completion_script(shell, build_parser())


def test_bash_lists_every_command_and_registers_complete():
    out = script("bash")
    for cmd in ("run", "validate", "init", "completion"):
        assert cmd in out
    assert "complete -F _lemontage lemontage" in out


def test_bash_offers_flags_as_usage_hints():
    out = script("bash")
    assert "--var" in out  # run's flag
    assert "--clean" in out
    assert "--force" in out  # init's flag


def test_bash_completes_only_yaml_files_for_file_commands():
    out = script("bash")
    assert "compgen -f" in out  # run/validate complete file paths
    # ...but restricted to pipeline files (plus directories to descend into)
    assert "!*.yaml" in out
    assert "!*.yml" in out
    assert "compgen -d" in out


def test_fish_completes_only_yaml_files_for_file_commands():
    out = script("fish")
    assert "__fish_complete_suffix .yaml" in out
    assert "__fish_complete_suffix .yml" in out


def test_zsh_is_a_compdef():
    out = script("zsh")
    assert out.startswith("#compdef lemontage")
    assert "_files -g" in out  # yaml file completion


def test_fish_registers_subcommands():
    out = script("fish")
    assert "complete -c lemontage" in out
    assert "-a run" in out


def test_unknown_shell_raises():
    with pytest.raises(ValueError):
        script("powershell")


def test_cli_completion_outputs_script(capsys):
    assert main(["completion", "bash"]) == 0
    assert "complete -F _lemontage lemontage" in capsys.readouterr().out


def test_cli_completion_rejects_unknown_shell():
    # argparse choices reject the value before our code runs.
    with pytest.raises(SystemExit):
        main(["completion", "tcsh"])
