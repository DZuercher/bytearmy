"""The tools a pane can run. Each tool is one TOML file in `<root>/.tools`; a file in
`<project>/.tools` replaces the one with the same name in that project."""

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

from bytearmy.config import ClaudeSettings, ToolSettings
from bytearmy.models import Pane, Parameter, Tool

log = logging.getLogger(__name__)

TOOLS_DIR = ".tools"

HEADER = """\
# A bytearmy tool. The file name is its id. A file of the same name in
# <project>/.tools replaces this one in that project.
#   name        shown in the command dialogue
#   kind        "terminal" (default): a program in a terminal pane
#               "web": a page in a browser pane
#               "external": a program with a window of its own; no pane
#               "log": adds the parameter `entry` to the task's log; no pane
#               "diff": the changes of the task's clone in diffle (https://diffle.app),
#               to review and comment on.
#               Closing the pane sends the open comments to the master agent.
#   command     terminal and external: run by your shell in the tool's directory.
#               For a terminal, empty means the shell itself.
#               {session} is replaced by an id that stays the same across restarts,
#               {<parameter>} by what was entered for that parameter.
#   resume      run instead of `command` when the pane is restored after a restart
#   agent       true: the program is an agent. It gets a name, {agent}, a file with
#               bytearmy's instructions, {instructions}, and Claude's permission flags
#               from config.toml, {permissions}.
#   spawn       agent: run instead of `command` when another agent starts this one;
#               {prompt} is what it asks the new agent to do
#   url         web: the page. Besides the parameters it can hold {directory},
#               {files} (the task directory, served by bytearmy), {log} (the task's
#               log as a page) and {service}.
#   directory   "task" (default) or "repo": the clone, if the task has one
#   callers     who may open the tool: ["user", "agent"] (default)
#   notify      terminal: true sends the end of its program, with the last lines it
#               printed, as a message to the agent that opened it, or else to the master
#   [[parameters]] with name, label, path = true for a file below the task directory
#               and default = what is used if nothing is entered ({github_page}: the
#               page of the project; {task_page}: the pull request of the task's branch,
#               or the page of the project if there is none)
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
PYCHARM = "open -a PyCharm" if MACOS else "pycharm"
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
CLAUDE_FLAGS = "--name {agent} --append-system-prompt-file {instructions} {permissions}"

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
    "lazygit": 'name = "Lazygit"\ndirectory = "repo"\ncommand = "lazygit"\n',
    "webpage": (
        'name = "Webpage"\n'
        'kind = "web"\n'
        'url = "{url}"\n'
        "\n"
        "[[parameters]]\n"
        'name = "url"\n'
        'label = "URL"\n'
        'default = "{task_page}"\n'
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
    "vscode": (
        'name = "VSCode"\n'
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
    "pycharm": (
        f'name = "PyCharm"\nkind = "external"\ndirectory = "repo"\ncommand = "{PYCHARM} ."\n'
        "# The pixi env in the clone that PyCharm gets as the interpreter of the project,\n"
        "# installed first if it is not there yet; empty: none.\n"
        'interpreter = ".pixi/envs/default"\n'
    ),
    "log": ('name = "Log"\nkind = "log"\n\n[[parameters]]\nname = "entry"\nlabel = "Log entry"\n'),
    "show-log": 'name = "Show Log"\nkind = "web"\nurl = "{log}"\n',
    "diff": 'name = "Review Changes"\nkind = "diff"\ndirectory = "repo"\n',
    "event_listener": (
        'name = "Event Listener"\n'
        'directory = "repo"\n'
        'callers = ["agent"]\n'
        "notify = true\n"
        "# `command` runs until the event happens, e.g. `gh pr checks 12 --watch`.\n"
        'command = "sh -c {command}"\n'
        "\n"
        "[[parameters]]\n"
        'name = "event"\n'
        'label = "What it waits for"\n'
        "\n"
        "[[parameters]]\n"
        'name = "command"\n'
        'label = "Command that ends when the event happens"\n'
    ),
}


# Fingerprints of the files earlier versions wrote (m3, m4, m5, #10, #11, diffle; macOS and Linux). A file
# that still has one was never edited, and is replaced by the current version.
EARLIER = {
    "terminal": {
        "30b737f89737ed7f1b39205ae4433539e3ea837eebb6b8afd4c0c42c2195b02a",
        "878e8097727c2ffc5dd4ad05345e2aae2cddceecfb192416c890cabd383e648b",
        "f79a0a7e2bfc39caed7cf655e2f2afff5e08cb51612eb087c100b2a61fdee0d2",
        "10d3a02ae7e257a83dde8466d06cb3aa69ce5b16be7cad6cf95fc9c10105cbc2",
        "e519a0f5ab5e0e24aaee9889ce46b645a6e726a0068b327e59a85dfe529110e5",
        "b70b936f401128c2b61243e6308977c8d5ae137ba07b496ff5be7f27b1f1e3f5",
        "af9a947c223fc4fdae3f32d946f5b8049698984d9a80dfa4f54943cba4ef360d",
    },
    "claude": {
        "853e7b4147fdd6c899fcc0cbf62bebd01e0584797ee836d31067558760cce5c1",
        "28e17bf121edfed84b4486e018d1564072c3aa2454b1cc64b148f46e1ed6d556",
        "d61c2c4d0afa8f80fd86edeceba6c3cc5eb310fe42ef75669ec27acf18b0d0bf",
        "ae0b3a3911d6f8fc710c47da0e4e0eea85c193b47232b23413f36e81313df5a4",
        "660c111d8f09c6687459da6c5745eabf6996a5d53546d0e8c178c8bdac8d9787",
        "1b6b70a1079a6a47a095943fc174f57108f803c014d459e26c875458ca85df21",
        "9df6bae2d882fa5b2e987fd9233a2d2c23ef27dd96ddae4bc8aa073bb4c06c39",
        "128458216a1b0a25c75b3314819e7e7289434d55f0c8ce6470452274cd7e8ed6",
    },
    "vim": {
        "eb5b624575842e280a1f807756a723dc74be432b0c4e479d9718d7911b516114",
        "f8ef1b57eddbe2f97dfb76f747bc34f23620de28200cc8cbf7fdbd7b7015d9b8",
        "1cf2de368f083b1ed2a52273d87684787474bb64e395a12bd642ac0e4cbc4a87",
        "3d8b8bc452a96af36ec005c125cc86f671b2f932df1c16b13048b29501597522",
        "65b58ecd3483cc13ab771275d5bc661ac59dcd6fe7aea9ec7ba37484a4b983be",
        "b2689242ab169d225b1a1cb1abc54f3a1c64d240616f0ed4e4a2211cfcf3369c",
        "18b2717861a8f2d8152dc326b27fcb97bd006b1e8e467a05dcac88f8e6369c3d",
    },
    "webpage": {
        "8140c3ef66b8e098021dbbb777d41a480fc9673a0765c76029cdd835e92e0665",
        "1816f0a2cefbb056add28f57963762ab4804b0c5e4bc8dd5bb87f3e594110320",
        "705df4429ae7deffa29983a004841598fc886ff21c157afecc6fabc464b6492c",
        "3b1880380bb2a7a2ef15b8aaa2bb27d2ab2ca5272647b872fa0cfbf130bdc776",
        "71e69a2c10328ddb61c40bc8072bb9804ba56ed83ab72d23393927c25a11e430",
        "cc621084fc73612c658aeb59f0c3b0840b7a3e8ab1c875db513c78a2295be558",
    },
    "html": {
        "67cb2d82c4660e7d4ae9e26fcb960b8ba456b5cbf92878a1729730619e785539",
        "d217fb7bf52bd6ae49d63d17ec884c537f36b7fe6ed07f0a4a593b96b78b0d09",
        "b3a6dbbbcd40fbc127ecf5d48983bbbc9b9afa92fbb0d64c618d69b46002a5ba",
        "55f0d657abf889e40c6ed4fd8d79cf9ce336783d40ace731b70b9cc2758c5cf8",
        "ce34c0c1f86a1c2571b1f45072e6e922b144fcdd41a2491b752d783d245edbfc",
        "fc70c0b29d3689294021dd6fe26f0e84021ab1589cf869450b280425529c8104",
    },
    "vscode": {
        "33ffe7931183a4c7e00e7b2e1600d0a4d36e6167d0a8005c6fad486621570a27",
        "63c610bb9ea549a1643c589dabe3b2818c24c30b9971b70d46e0d0052c51ec29",
        "1ac2c4a738ebfb09e4bb57114c6f036c3abfa1605321518d2d0271c5c6767fa4",
        "8834054b8f1d2b2232c93cfa8480a66bd73f1a919a66d207bacadedb75bc738a",
        "1a3287df953cbc3125976c05b933e0be41e9fb662c075d4bb214d6b578ee6448",
        "74bdb0e78765c764bafb94a11ca4df235f35a0f1236915f0a0a40f32bf1c7a22",
        "10d4856d01c7c411845aface54c1d9beffa29e7d3e6ba7feb0da036cf6b5ee35",
    },
    "explorer": {
        "123b6b37c169a431418c0fe6dc96e168679d22a32fc3d7ca402df9f6afaab07a",
        "4bf639b0cf4d8f3d317e04c550495f429af9953a02db66222f9b48b996123e89",
        "440b4ccddb873df3eef96c75b1184c8561ead106d9936a0e3f85fa6e7057444d",
        "541ded652a49c8b3fd9cdbc780ea1dbcbbaa2a2233cea9bd0c3e0dc56468a6b4",
        "0363df229c06ae2f15c65b23869d0c93e4c856c82cf2a99432d0acd65bbfb67c",
        "af6d09d53d4e4facf797c227b693da0ac2664f7409575c435137dc80c1074178",
        "0cbec5d11aa66ca9d59e64942e2bfea276f913b4994a50add6afbf2847f8e9e5",
        "6a43a59f5a7d7b492e14fdabacc86b19008bb117714cf7e92a879e2ffa130843",
        "da92a21a0388f30cd1f19b93e1303c909d9e8a3d16fd1ec74f11ef5ef2d2aa96",
        "e6c186d02e48f9734ed5a7e6d3e4d74eea126631c7801e5fd65ab53a6a921b62",
        "a56cdb26956652e6319b3cdbd0c4e1d365425d20fe29489587d8a0f3b3a387e0",
    },
    "pycharm": {
        "cb020c375e6e205f6ebb51a1766ddca5bed54766562d7dc1ddf604d6b4aec07c",
        "a0f17e51939b00abc61572445d19330f4d38d4128d36ee20e4a10827991491d5",
        "731fec50f27c0a4b9066b4459a3fe71d964b31fc1cb9f124c2bfaa740250135b",
        "80c05a6a8f27735365cc0ab7c5f33a07302cbac121af36ef959c590fd6e3453d",
        "1e72de460c19588320173059e2382e3707a6f202aa09a7323a4c05b7c35119eb",
        "915ebed46753df1b214005cc6f9a098afdfa201630d36958086a12a6b165e6fe",
        "ed74822330e5c74b6fe3eb27da1176db0dd93b256bc93b2f72b25cca5b5a483b",
        "83823aa7f9f237effbbb8c33fc73b0a578d2d3dd54fd54ee07c29f6df95930d0",
        "ed7f0e33565c99106b589bc31a2f97b747bb26a9f065fbdb082fe9c20c910abd",
    },
    "log": {
        "f947639546c1d58bdddaf494b2b7017cb132524079a62d440659d0c1f922dd8d",
        "5fdb16949044ebe125eb23d23a746daa0fa40e16884ef87515b4247033312aaf",
        "9cdaeadab1d48d11900ad3bad7ace8367679f7ccb02d6127a2ea14c4d736780b",
        "05e2f2b33948a9931cec3c2705d4d954ee0293d7c418969dfff276d26156bd65",
    },
    "show-log": {
        "5b21bc617bacd2b99831b5b03ec168c5d1abca8c77256182093a8ffcf3eafc6e",
        "1a91372c6576af6afc7f321584a93074a9690c7c88583ccfa97d181bce51a0b0",
        "57a5e0d990044d0d99188947cc228c1e046a66b313899acd702aa0f58639b71c",
        "b1f326bf70d7c91ddc2004878b20d51f28890a5100b86e7b6498e0fad651065d",
    },
    "diff": {
        "fb8fe92329d5b49b199628004cf638b6eaaa43569a9942256720ab814b125400",
        "daf403c8efe4fdc1a69c39831f41f4d20d608206eecc3a3ba7d884e62fa8e279",
    },
    "lazygit": {
        "c3e26fbf05fe83d9a257e61aed23bb2b425c9689031c0bf4d9eb8a2829b6913c",
        "ef5172616dbb4f694885a3c5a2ba33b3caa843beed8b601407973f5ee58addd1",
    },
    "event_listener": set(),
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


def load_tools(
    root: Path, project: Path | None = None, settings: Mapping[str, ToolSettings] | None = None
) -> dict[str, Tool]:
    """The tools by id; a file that cannot be read as a tool is skipped.

    In a `project`, its own tool files replace those of the root with the same name.
    `settings` (the `[tools]` of config.toml) leave out tools and replace fields of others.
    """
    settings = settings or {}
    tools = {}
    for directory in [root, project] if project else [root]:
        for file in sorted((directory / TOOLS_DIR).glob("*.toml")):
            setting = settings.get(file.stem)
            if setting is not None and not setting.enabled:
                tools.pop(file.stem, None)
                continue
            try:
                fields = tomllib.loads(file.read_text())
                if setting is not None:
                    fields.update(setting.fields())
                tools[file.stem] = Tool(**fields, id=file.stem)
            except (OSError, tomllib.TOMLDecodeError, ValidationError, TypeError) as error:
                log.warning("Skipping tool %s: %s", file, error)
    return tools


def fill(template: str, values: Mapping[str, str]) -> str:
    """`template` with every `{name}` that has a value replaced by it, in one pass."""
    return re.sub(r"\{(\w+)\}", lambda match: values.get(match[1], match[0]), template)


def command_line(
    tool: Tool,
    pane: Pane,
    restoring: bool,
    instructions: Path | None = None,
    permissions: str = "",
) -> str:
    """The shell command of a pane, with the session id and the parameters filled in.

    An agent's command also gets its name, the file with its instructions and Claude's
    permission flags (already quoted); one that another agent started gets that agent's
    prompt the first time.
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
    quoted = {name: shlex.quote(value) for name, value in values.items()}
    if tool.agent:
        quoted["permissions"] = permissions
    return fill(line, quoted)


def external_command(tool: Tool, values: Mapping[str, str]) -> str:
    """The shell command of a tool that opens a window of its own."""
    return fill(tool.command, {name: shlex.quote(value) for name, value in values.items()})


def default_value(parameter: Parameter, github_page: str, pull_request: str = "") -> str:
    """What a parameter is taken to be if nothing is entered for it.

    {task_page} is the task's pull request, or the project's page if it has none.
    """
    task_page = pull_request or github_page
    pages = {"github_page": github_page, "task_page": task_page}
    if any(f"{{{name}}}" in parameter.default and not page for name, page in pages.items()):
        return ""  # the project has no page: there is nothing to offer
    return fill(parameter.default, pages)


def page_url(
    tool: Tool, values: Mapping[str, str], directory: Path, files: str, service: str, log: str = ""
) -> str:
    """The page of a web pane: the tool's url with its parts filled in.

    Paths are escaped; anything else is taken as it was entered. A page without a
    scheme is taken to be https, and only http and https pages are shown.
    """
    paths = {parameter.name for parameter in tool.parameters if parameter.path}
    entered = {name: quote(value) if name in paths else value for name, value in values.items()}
    parts = {"directory": quote(str(directory)), "files": files, "service": service, "log": log}
    url = fill(tool.url, {**entered, **parts})
    if not SCHEME.match(url):
        url = f"https://{url}"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{url} is not a web page.")
    return url


def as_configured(tool: Tool, settings: ClaudeSettings) -> Tool:
    """An agent tool with the command, resume and spawn line that config.toml sets for the
    agent instead, if it sets them."""
    lines = {"command": settings.command, "resume": settings.resume}
    lines["spawn"] = getattr(settings, "spawn", "")
    return tool.model_copy(update={name: line for name, line in lines.items() if line})
