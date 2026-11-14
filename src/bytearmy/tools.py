"""The tools a pane can run. Each tool is one TOML file in `<root>/.tools`."""

from __future__ import annotations

import logging
import shlex
import tomllib
from pathlib import Path

from pydantic import ValidationError

from bytearmy.models import Pane, Tool

log = logging.getLogger(__name__)

TOOLS_DIR = ".tools"

HEADER = """\
# A bytearmy tool. The file name is its id.
#   name        shown in the command dialogue
#   command     run by your shell in a new pane; empty means the shell itself.
#               {session} is replaced by an id that stays the same across restarts,
#               {<parameter>} by what was entered for that parameter.
#   resume      run instead of `command` when the pane is restored after a restart
#   directory   "task" (default) or "repo": the clone, if the task has one
#   [[parameters]] with name, label and path = true for a file below the task directory
"""

PREMADE = {
    "terminal": 'name = "Terminal"\n',
    "claude": (
        'name = "Claude"\n'
        'directory = "repo"\n'
        'command = "claude --session-id {session}"\n'
        'resume = "claude --resume {session} || claude --session-id {session}"\n'
    ),
    "vim": (
        'name = "Vim"\n'
        'command = "vim {file}"\n'
        "\n"
        "[[parameters]]\n"
        'name = "file"\n'
        'label = "File, relative to the task directory"\n'
        "path = true\n"
    ),
}


def ensure_premade(root: Path) -> None:
    """Write the tools bytearmy comes with, unless a file of that name already exists."""
    directory = root / TOOLS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for tool_id, text in PREMADE.items():
        file = directory / f"{tool_id}.toml"
        if not file.exists():
            file.write_text(HEADER + text)


def load_tools(root: Path) -> dict[str, Tool]:
    """The tools by id; a file that cannot be read as a tool is skipped."""
    tools = {}
    for file in sorted((root / TOOLS_DIR).glob("*.toml")):
        try:
            tools[file.stem] = Tool(**tomllib.loads(file.read_text()), id=file.stem)
        except (OSError, tomllib.TOMLDecodeError, ValidationError, TypeError) as error:
            log.warning("Skipping tool %s: %s", file.name, error)
    return tools


def command_line(tool: Tool, pane: Pane, restoring: bool) -> str:
    """The shell command of a pane, with the session id and the parameters filled in."""
    line = tool.resume if restoring and tool.resume else tool.command
    for name, value in {"session": pane.session, **pane.values}.items():
        line = line.replace("{" + name + "}", shlex.quote(value))
    return line
