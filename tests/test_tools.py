"""Checks for the tool files in `<root>/.tools`."""

from __future__ import annotations

from bytearmy.models import Pane, Parameter, Tool
from bytearmy.tools import command_line, ensure_premade, load_tools


def pane(**fields) -> Pane:
    return Pane(id="p1", tool="x", title="x", session="1234-abcd", **fields)


def test_the_premade_tools_are_written_once_and_never_overwritten(tmp_path):
    ensure_premade(tmp_path)
    assert sorted(file.name for file in (tmp_path / ".tools").iterdir()) == [
        "claude.toml",
        "terminal.toml",
        "vim.toml",
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
    assert tools["claude"] == Tool(
        id="claude",
        name="Claude",
        directory="repo",
        command="claude --session-id {session}",
        resume="claude --resume {session} || claude --session-id {session}",
    )
    assert tools["vim"] == Tool(
        id="vim",
        name="Vim",
        command="vim {file}",
        parameters=[
            Parameter(name="file", label="File, relative to the task directory", path=True)
        ],
    )


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
