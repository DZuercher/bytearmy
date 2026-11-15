"""Checks for the tool files in `<root>/.tools`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bytearmy.models import Pane, Parameter, Tool
from bytearmy.tools import (
    EARLIER,
    HEADER,
    PREMADE,
    command_line,
    default_value,
    ensure_premade,
    external_command,
    fingerprint,
    load_tools,
    page_url,
)

MACOS = sys.platform == "darwin"
VSCODE_IN_APP = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"


def pane(**fields) -> Pane:
    return Pane(id="p1", tool="x", title="x", session="1234-abcd", **fields)


def test_the_premade_tools_are_written_once_and_never_overwritten(tmp_path):
    ensure_premade(tmp_path)
    assert sorted(file.name for file in (tmp_path / ".tools").iterdir()) == [
        "claude.toml",
        "diff.toml",
        "explorer.toml",
        "html.toml",
        "lazygit.toml",
        "log.toml",
        "pycharm.toml",
        "show-log.toml",
        "terminal.toml",
        "vim.toml",
        "vscode.toml",
        "webpage.toml",
    ]

    (tmp_path / ".tools" / "vim.toml").write_text('name = "My editor"\ncommand = "nvim {file}"\n')
    (tmp_path / ".tools" / "terminal.toml").unlink()
    ensure_premade(tmp_path)

    tools = load_tools(tmp_path)
    assert tools["vim"].name == "My editor"
    assert tools["terminal"].name == "Terminal"


def test_the_premade_tools_say_how_they_run(tmp_path):
    ensure_premade(tmp_path)

    tools = load_tools(tmp_path)

    assert tools["terminal"] == Tool(id="terminal", name="Terminal")
    flags = "--name {agent} --append-system-prompt-file {instructions} {permissions}"
    assert tools["claude"] == Tool(
        id="claude",
        name="Claude",
        directory="repo",
        agent=True,
        command=f"claude --session-id {{session}} {flags}",
        resume=f"claude --resume {{session}} {flags} || claude --session-id {{session}} {flags}",
        spawn=f"claude --session-id {{session}} {flags} {{prompt}}",
    )
    assert tools["vim"] == Tool(
        id="vim",
        name="Vim",
        command="vim {file}",
        parameters=[
            Parameter(name="file", label="File, relative to the task directory", path=True)
        ],
    )
    assert tools["terminal"].callers == ["user", "agent"]


def test_an_agent_whose_tool_has_no_spawn_line_starts_with_its_command():
    tool = Tool(id="bot", name="Bot", agent=True, command="bot {agent}")
    pane = Pane(id="p1", tool="bot", title="Bot", agent="a-b-c", prompt="Do it.")

    assert command_line(tool, pane, restoring=False, instructions=Path("x.md")) == "bot a-b-c"


def test_a_premade_file_nobody_edited_is_brought_up_to_date(tmp_path, monkeypatch):
    tools = tmp_path / ".tools"
    tools.mkdir()
    earlier = "written by an earlier bytearmy\n"
    (tools / "claude.toml").write_text(earlier)
    (tools / "vim.toml").write_text("changed by the user\n")
    monkeypatch.setitem(EARLIER, "claude", {fingerprint(earlier)})
    monkeypatch.setitem(EARLIER, "vim", {fingerprint(earlier)})

    ensure_premade(tmp_path)

    assert (tools / "claude.toml").read_text() == HEADER + PREMADE["claude"]
    assert (tools / "vim.toml").read_text() == "changed by the user\n"
    assert (tools / "terminal.toml").read_text() == HEADER + PREMADE["terminal"]


def test_the_premade_web_and_external_tools_say_what_they_show(tmp_path):
    ensure_premade(tmp_path)

    tools = load_tools(tmp_path)

    assert tools["webpage"] == Tool(
        id="webpage",
        name="Webpage",
        kind="web",
        url="{url}",
        parameters=[Parameter(name="url", label="URL", default="{task_page}")],
    )
    assert tools["html"] == Tool(
        id="html",
        name="HTML",
        kind="web",
        callers=["agent"],
        url="{files}/{file}",
        parameters=[
            Parameter(name="file", label="HTML file, relative to the task directory", path=True)
        ],
    )
    program = f"\"$(command -v code || echo '{VSCODE_IN_APP}')\"" if MACOS else "code"
    assert tools["vscode"] == Tool(
        id="vscode",
        name="VSCode",
        kind="web",
        directory="repo",
        url="{service}&folder={directory}",
        service={
            "command": f"{program} serve-web --host 127.0.0.1 --port {{port}}"
            " --connection-token {token} --accept-server-license-terms",
            "url": "http://127.0.0.1:{port}/?tkn={token}",
        },
    )
    assert tools["explorer"] == Tool(
        id="explorer",
        name="Explorer",
        kind="external",
        command="open ." if MACOS else "xdg-open .",
    )
    assert tools["pycharm"] == Tool(
        id="pycharm",
        name="PyCharm",
        kind="external",
        directory="repo",
        command="open -a PyCharm ." if MACOS else "pycharm .",
    )


@pytest.mark.skipif(not MACOS, reason="only macOS keeps VS Code's command line tool in the app")
def test_vscode_takes_vs_code_from_the_path_or_else_from_inside_the_app(tmp_path):
    ensure_premade(tmp_path)
    program = load_tools(tmp_path)["vscode"].service.command.split(" serve-web")[0]
    on_path = tmp_path / "bin" / "code"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)

    def found(path: str) -> str:
        shell = ["/bin/sh", "-c", f"echo {program}"]
        done = subprocess.run(shell, env={"PATH": path}, check=True, capture_output=True, text=True)
        return done.stdout

    assert found(f"{on_path.parent}:/usr/bin:/bin") == f"{on_path}\n"
    assert found("/usr/bin:/bin") == f"{VSCODE_IN_APP}\n"


def test_files_that_are_not_tools_are_skipped(tmp_path):
    directory = tmp_path / ".tools"
    directory.mkdir()
    (directory / "good.toml").write_text('name = "Good"\n')
    (directory / "not-toml.toml").write_text("name = \n")
    (directory / "no-name.toml").write_text('command = "ls"\n')
    (directory / "own-id.toml").write_text('id = "other"\nname = "Own id"\n')
    (directory / "backup.toml.bak").write_text('name = "Backup"\n')
    (directory / "folder.toml").mkdir()

    assert list(load_tools(tmp_path)) == ["good"]


def test_a_project_tool_file_replaces_the_one_of_the_root(tmp_path):
    root, project = tmp_path, tmp_path / "demo"
    for directory in (root / ".tools", project / ".tools"):
        directory.mkdir(parents=True)
    (root / ".tools" / "claude.toml").write_text('name = "Claude"\nspawn = "claude {prompt}"\n')
    (root / ".tools" / "vim.toml").write_text('name = "Vim"\n')
    (project / ".tools" / "claude.toml").write_text('name = "Work"\nspawn = "work {prompt}"\n')

    tools = load_tools(root, project)
    assert (tools["claude"].name, tools["claude"].spawn) == ("Work", "work {prompt}")
    assert tools["vim"].name == "Vim"
    assert load_tools(root)["claude"].spawn == "claude {prompt}"


def test_a_web_tool_needs_a_url_and_an_external_tool_a_command(tmp_path):
    directory = tmp_path / ".tools"
    directory.mkdir()
    (directory / "page.toml").write_text('name = "P"\nkind = "web"\nurl = "https://example.org"\n')
    (directory / "no-url.toml").write_text('name = "No url"\nkind = "web"\n')
    (directory / "window.toml").write_text('name = "W"\nkind = "external"\ncommand = "true"\n')
    (directory / "no-command.toml").write_text('name = "No command"\nkind = "external"\n')

    assert list(load_tools(tmp_path)) == ["page", "window"]


def test_command_line_fills_in_the_session_and_the_quoted_parameters():
    vim = Tool(id="vim", name="Vim", command="vim {file}")
    claude = Tool(
        id="c", name="C", command="c --new {session}", resume="c --old {session} || c {session}"
    )

    assert command_line(vim, pane(values={"file": "my notes; rm -rf x.md"}), restoring=False) == (
        "vim 'my notes; rm -rf x.md'"
    )
    assert command_line(claude, pane(), restoring=False) == "c --new 1234-abcd"
    assert command_line(claude, pane(), restoring=True) == "c --old 1234-abcd || c 1234-abcd"
    assert command_line(vim, pane(values={"file": "a.md"}), restoring=True) == "vim a.md"
    assert command_line(Tool(id="t", name="T"), pane(), restoring=False) == ""


def test_external_command_fills_in_the_quoted_parameters():
    tool = Tool(id="e", name="E", kind="external", command="open {what} {unknown}")

    assert external_command(tool, {"what": "my file; rm x"}) == "open 'my file; rm x' {unknown}"


def test_a_default_can_be_the_page_of_the_project():
    page = "https://github.com/org/repo"

    assert default_value(Parameter(name="url", default="{github_page}/issues"), page) == (
        "https://github.com/org/repo/issues"
    )
    assert default_value(Parameter(name="url", default="{github_page}/issues"), "") == ""
    assert default_value(Parameter(name="url", default="https://example.org"), "") == (
        "https://example.org"
    )
    assert default_value(Parameter(name="url"), page) == ""


def shown(tool: Tool, **values: str) -> str:
    return page_url(
        tool,
        values,
        Path("/root/my project/task"),
        files="http://core/files/key/demo/work",
        service="http://ide/?tkn=secret",
    )


def test_page_url_fills_in_its_parts_and_escapes_paths():
    html = Tool(
        id="h",
        name="H",
        kind="web",
        url="{files}/{file}",
        parameters=[Parameter(name="file", path=True)],
    )
    ide = Tool(id="i", name="I", kind="web", url="{service}&folder={directory}")
    page = Tool(id="p", name="P", kind="web", url="{url}", parameters=[Parameter(name="url")])

    assert shown(html, file="out/my report.html") == (
        "http://core/files/key/demo/work/out/my%20report.html"
    )
    assert shown(ide) == "http://ide/?tkn=secret&folder=/root/my%20project/task"
    # What was entered is neither escaped nor filled in a second time.
    assert shown(page, url="https://example.org/a b?q={files}") == (
        "https://example.org/a b?q={files}"
    )
    # A parameter cannot take the place of a part that bytearmy fills in.
    assert shown(html, file="a.html", files="https://elsewhere.example") == (
        "http://core/files/key/demo/work/a.html"
    )


@pytest.mark.parametrize(
    ("entered", "page"),
    [
        ("https://example.org/x", "https://example.org/x"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("example.org/x?next=https://other", "https://example.org/x?next=https://other"),
        ("localhost:8000", "https://localhost:8000"),
    ],
)
def test_a_page_without_a_scheme_is_taken_as_https(entered, page):
    tool = Tool(id="p", name="P", kind="web", url="{url}", parameters=[Parameter(name="url")])

    assert shown(tool, url=entered) == page


@pytest.mark.parametrize("entered", ["file:///etc/passwd", "ftp://example.org", "chrome://gpu"])
def test_only_http_and_https_pages_are_shown(entered):
    tool = Tool(id="p", name="P", kind="web", url="{url}", parameters=[Parameter(name="url")])

    with pytest.raises(ValueError, match="is not a web page"):
        shown(tool, url=entered)


def test_a_default_can_be_the_pull_request_of_the_task_or_else_the_project_page():
    page, pull = "https://github.com/org/repo", "https://github.com/org/repo/pull/7"
    parameter = Parameter(name="url", default="{task_page}")

    assert default_value(parameter, page, pull) == pull
    assert default_value(parameter, page) == page
    assert default_value(parameter, "") == ""
