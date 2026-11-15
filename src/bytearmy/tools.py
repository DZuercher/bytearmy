"""The tools a pane can run. Each tool is one TOML file in `<root>/.tools`."""

from __future__ import annotations

import hashlib
import logging
import re
import shlex
import sys
import tomllib
from collections.abc import Collection, Mapping
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
#   agent       true: the program is an agent. It gets a name, {agent}, and a file with
#               bytearmy's instructions, {instructions}.
#   spawn       agent: run instead of `command` when another agent starts this one;
#               {prompt} is what it asks the new agent to do
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
CLAUDE_FLAGS = "--name {agent} --append-system-prompt-file {instructions}"

PREMADE = {
    "terminal": 'name = "Terminal"\n',
    "claude": (
        'name = "Claude"\n'
        'directory = "repo"\n'
        "agent = true\n"
        f'command = "claude --session-id {{session}} {CLAUDE_FLAGS}"\n'
        f'resume = "claude --resume {{session}} {CLAUDE_FLAGS}'
        f' || claude --session-id {{session}} {CLAUDE_FLAGS}"\n'
        "# Agents that other agents start. `claude` may ask which account to use first;\n"
        "# put the command of the account they should use here instead.\n"
        f'spawn = "claude --session-id {{session}} {CLAUDE_FLAGS} {{prompt}}"\n'
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


# Fingerprints of the files earlier versions wrote (m3, m4; macOS and Linux). A file that
# still has one was never edited, and is replaced by the current version.
EARLIER = {
    "terminal": {
        "30b737f89737ed7f1b39205ae4433539e3ea837eebb6b8afd4c0c42c2195b02a",
        "878e8097727c2ffc5dd4ad05345e2aae2cddceecfb192416c890cabd383e648b",
    },
    "claude": {
        "853e7b4147fdd6c899fcc0cbf62bebd01e0584797ee836d31067558760cce5c1",
        "28e17bf121edfed84b4486e018d1564072c3aa2454b1cc64b148f46e1ed6d556",
    },
    "vim": {
        "eb5b624575842e280a1f807756a723dc74be432b0c4e479d9718d7911b516114",
        "f8ef1b57eddbe2f97dfb76f747bc34f23620de28200cc8cbf7fdbd7b7015d9b8",
    },
    "webpage": {"8140c3ef66b8e098021dbbb777d41a480fc9673a0765c76029cdd835e92e0665"},
    "html": {"67cb2d82c4660e7d4ae9e26fcb960b8ba456b5cbf92878a1729730619e785539"},
    "ide": {
        "4803194c0a2d9eed46b3db8f2631c4be644c0df691dbc776e7662eb30625afe3",
        "d21a8e27826fb881a6731ba7567a595cfcd8c834f5cc56c93aadcd05cb9a5bc8",
    },
    "explorer": {
        "123b6b37c169a431418c0fe6dc96e168679d22a32fc3d7ca402df9f6afaab07a",
        "4bf639b0cf4d8f3d317e04c550495f429af9953a02db66222f9b48b996123e89",
    },
}


class ToolFailed(RuntimeError):
    """A tool's program could not be started; the message says why."""


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_premade(file: Path, text: str, earlier: Collection[str]) -> None:
    """Write a file bytearmy comes with, unless the user has changed it.

    The file is written if it is missing, or if it still holds what an earlier version
    of bytearmy wrote: then nobody has edited it.
    """
    if file.exists() and fingerprint(file.read_text()) not in earlier:
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def ensure_premade(root: Path) -> None:
    """Write the tools bytearmy comes with, unless the user has changed their files."""
    for tool_id, text in PREMADE.items():
        write_premade(root / TOOLS_DIR / f"{tool_id}.toml", HEADER + text, EARLIER[tool_id])


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


def command_line(tool: Tool, pane: Pane, restoring: bool, instructions: Path | None = None) -> str:
    """The shell command of a pane, with the session id and the parameters filled in.

    An agent's command also gets its name and the file with its instructions; one that
    another agent started gets that agent's prompt the first time.
    """
    if restoring and tool.resume:
        line = tool.resume
    elif pane.prompt and tool.spawn:
        line = tool.spawn
    else:
        line = tool.command
    values = {"session": pane.session, **pane.values}
    if tool.agent:
        values.update(agent=pane.agent, instructions=str(instructions), prompt=pane.prompt)
    return fill(line, {name: shlex.quote(value) for name, value in values.items()})


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
