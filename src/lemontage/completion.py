"""Generate shell completion scripts for the ``lemontage`` CLI.

The scripts are built by introspecting the argparse parser (see
``cli.build_parser``), so they stay correct as commands and options change — no
second list to keep in sync. Each command's flags are offered as completions
(usage hints), and commands that take a pipeline file complete ``*.yaml`` /
``*.yml`` paths.

    eval "$(lemontage completion bash)"      # bash: add to ~/.bashrc
    lemontage completion zsh  > ~/.zfunc/_lemontage
    lemontage completion fish > ~/.config/fish/completions/lemontage.fish
"""

from __future__ import annotations

import argparse


class _Command:
    """The completion-relevant shape of one sub-command."""

    def __init__(self, name: str, options: list[str], takes_file: bool, choices: list[str]):
        self.name = name
        self.options = options
        self.takes_file = takes_file
        self.choices = choices  # positional value choices, e.g. completion's shells


def _commands(parser: argparse.ArgumentParser) -> list[_Command]:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands = []
    for name, subparser in sub.choices.items():
        options: list[str] = []
        takes_file = False
        choices: list[str] = []
        for action in subparser._actions:
            if action.option_strings:
                options.extend(action.option_strings)
            elif action.dest == "file":
                takes_file = True
            elif action.choices:
                choices = [str(c) for c in action.choices]
        commands.append(_Command(name, sorted(set(options)), takes_file, choices))
    return commands


def completion_script(shell: str, parser: argparse.ArgumentParser) -> str:
    """Return the completion script for ``shell`` (bash, zsh or fish)."""
    builders = {"bash": _bash, "zsh": _zsh, "fish": _fish}
    if shell not in builders:
        raise ValueError(f"unsupported shell '{shell}' (choose bash, zsh or fish)")
    return builders[shell](_commands(parser))


def _bash(commands: list[_Command]) -> str:
    names = " ".join(c.name for c in commands)
    cases = []
    for cmd in commands:
        if cmd.takes_file:
            opts = " ".join(cmd.options)
            # File commands complete *.yaml / *.yml plus directories (to descend
            # into them), not every file.
            body = (
                f'if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "{opts}" -- "$cur") ); '
                "else COMPREPLY=( $(compgen -f -X '!*.yaml' -- \"$cur\") "
                '$(compgen -f -X \'!*.yml\' -- "$cur") $(compgen -d -- "$cur") ); fi'
            )
        elif cmd.choices:
            words = " ".join(cmd.choices + cmd.options)
            body = f'COMPREPLY=( $(compgen -W "{words}" -- "$cur") )'
        else:
            body = f'COMPREPLY=( $(compgen -W "{" ".join(cmd.options)}" -- "$cur") )'
        cases.append(f"    {cmd.name}) {body} ;;")
    cases_block = "\n".join(cases)
    return f"""\
_lemontage() {{
  local cur cmd
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "{names} --version --help" -- "$cur") )
    return
  fi
  cmd="${{COMP_WORDS[1]}}"
  case "$cmd" in
{cases_block}
  esac
}}
complete -F _lemontage lemontage
"""


def _zsh(commands: list[_Command]) -> str:
    described = "\n".join(f"    '{c.name}'" for c in commands)
    cases = []
    for cmd in commands:
        specs = [f"'{opt}[option]'" for opt in cmd.options]
        if cmd.takes_file:
            specs.append("'*:pipeline:_files -g \"*.yaml *.yml\"'")
        elif cmd.choices:
            specs.append(f"'*:value:({' '.join(cmd.choices)})'")
        cases.append(f"    {cmd.name}) _arguments {' '.join(specs)} ;;")
    cases_block = "\n".join(cases)
    return f"""\
#compdef lemontage
_lemontage() {{
  local -a commands
  commands=(
{described}
  )
  if (( CURRENT == 2 )); then
    _describe 'command' commands
    return
  fi
  case "${{words[2]}}" in
{cases_block}
  esac
}}
_lemontage "$@"
"""


def _fish(commands: list[_Command]) -> str:
    lines = ["complete -c lemontage -f"]
    for cmd in commands:
        lines.append(
            f"complete -c lemontage -n __fish_use_subcommand -a {cmd.name} "
            f"-d 'lemontage {cmd.name}'"
        )
        seen = f"__fish_seen_subcommand_from {cmd.name}"
        for opt in cmd.options:
            if opt.startswith("--"):
                lines.append(f"complete -c lemontage -n '{seen}' -l {opt[2:]}")
        for choice in cmd.choices:
            lines.append(f"complete -c lemontage -n '{seen}' -a {choice}")
        if cmd.takes_file:
            # Re-enable file completion (disabled globally by `-f` above), but
            # only for *.yaml / *.yml pipeline files.
            for suffix in (".yaml", ".yml"):
                lines.append(
                    f"complete -c lemontage -n '{seen}' -k -a \"(__fish_complete_suffix {suffix})\""
                )
    return "\n".join(lines) + "\n"
