"""The tools a pane can run. Each tool is one TOML file in `<root>/.tools`."""

from __future__ import annotations

import logging
import re
import shlex
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from bytearmy.models import Pane, Parameter, Tool

log = logging.getLogger(__name__)

TOOLS_DIR = ".tools"

HEADER = """\
# A bytearmy tool. The file name is its id.
#   name        shown in the command dialogue
#   kind        "terminal" (default): a program in a terminal pane
#               "web": a page in a browser pane
#               "external": a program with a window of its own; no pane
#   command     terminal and external: run by your shell in the tool's directory.
#               For a terminal, empty means the shell itself.
#               {session} is replaced by an id that stays the same across restarts,
#               {<parameter>} by what was entered for that parameter.
#   resume      run instead of `command` when the pane is restored after a restart
#   url         web: the page. Besides the parameters it can hold {directory},
#               {files} (the task directory, served by bytearmy) and {service}.
#   directory   "task" (default) or "repo": the clone, if the task has one
#   callers     who may open the tool: ["user", "agent"] (default)
#   [[parameters]] with name, label, path = true for a file below the task directory
#               and default = what is used if nothing is entered ({github_page}: the
#               page of the project)
#   [service]   web: a program that all panes of the tool share, started when a pane
#               first needs it. `command` is run by /bin/sh with a free {port} and a
#               random {token}; `url` is where it then answers, and what {service} in
#               the tool's url stands for.
"""

MACOS = sys.platform == "darwin"
# macOS keeps VS Code's command line tool inside the app, often without a link on PATH.
VSCODE_IN_APP = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
VSCODE = f"\"$(command -v code || echo '{VSCODE_IN_APP}')\"" if MACOS else "code"
FILE_MANAGER = "open" if MACOS else "xdg-open"
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")

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
    "webpage": (
        'name = "Webpage"\n'
        'kind = "web"\n'
        'url = "{url}"\n'
        "\n"
        "[[parameters]]\n"
        'name = "url"\n'
        'label = "URL"\n'
        'default = "{github_page}"\n'
    ),
    "html": (
        'name = "HTML"\n'
        'kind = "web"\n'
        'callers = ["agent"]\n'
        'url = "{files}/{file}"\n'
        "\n"
        "[[parameters]]\n"
        'name = "file"\n'
        'label = "HTML file, relative to the task directory"\n'
        "path = true\n"
    ),
    "ide": (
        'name = "IDE"\n'
        'kind = "web"\n'
        'directory = "repo"\n'
        'url = "{service}&folder={directory}"\n'
        "\n"
        "[service]\n"
        f"command = '''{VSCODE} serve-web --host 127.0.0.1 --port {{port}}"
        " --connection-token {token} --accept-server-license-terms'''\n"
        'url = "http://127.0.0.1:{port}/?tkn={token}"\n'
    ),
    "explorer": f'name = "Explorer"\nkind = "external"\ncommand = "{FILE_MANAGER} ."\n',
}


class ToolFailed(RuntimeError):
    """A tool's program could not be started; the message says why."""


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


def fill(template: str, values: Mapping[str, str]) -> str:
    """`template` with every `{name}` that has a value replaced by it, in one pass."""
    return re.sub(r"\{(\w+)\}", lambda match: values.get(match[1], match[0]), template)


def command_line(tool: Tool, pane: Pane, restoring: bool) -> str:
    """The shell command of a pane, with the session id and the parameters filled in."""
    line = tool.resume if restoring and tool.resume else tool.command
    for name, value in {"session": pane.session, **pane.values}.items():
        line = line.replace("{" + name + "}", shlex.quote(value))
    return line


def external_command(tool: Tool, values: Mapping[str, str]) -> str:
    """The shell command of a tool that opens a window of its own."""
    return fill(tool.command, {name: shlex.quote(value) for name, value in values.items()})


def default_value(parameter: Parameter, github_page: str) -> str:
    """What a parameter is taken to be if nothing is entered for it."""
    if "{github_page}" in parameter.default and not github_page:
        return ""  # the project has no page: there is nothing to offer
    return fill(parameter.default, {"github_page": github_page})


def page_url(
    tool: Tool, values: Mapping[str, str], directory: Path, files: str, service: str
) -> str:
    """The page of a web pane: the tool's url with its parts filled in.

    Paths are escaped; anything else is taken as it was entered. A page without a
    scheme is taken to be https, and only http and https pages are shown.
    """
    paths = {parameter.name for parameter in tool.parameters if parameter.path}
    entered = {name: quote(value) if name in paths else value for name, value in values.items()}
    parts = {"directory": quote(str(directory)), "files": files, "service": service}
    url = fill(tool.url, {**entered, **parts})
    if not SCHEME.match(url):
        url = f"https://{url}"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{url} is not a web page.")
    return url
