"""Checks for workspaces: panes, their layout and the programs running in them."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from conftest import read_to_end, read_until, running, wait_until

from bytearmy.models import PaneOpen, ProjectMeta, Split, TaskCreate
from bytearmy.ptys import Pty
from bytearmy.store import Store
from bytearmy.tools import ToolFailed
from bytearmy.workspaces import (
    COULD_NOT_START,
    InvalidLayout,
    InvalidValues,
    NotStarted,
    PaneNotFound,
    UnknownTool,
    Workspaces,
    pane_env,
)

WAIT = "sleep 60"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def workspaces(store):
    made = Workspaces(store, shell=["/bin/sh"])
    yield made
    made.shutdown()


@pytest.fixture
def task(store) -> tuple[str, str]:
    """A started task, as (project, task)."""
    project = store.create_project(ProjectMeta(name="Demo")).slug
    store.create_task(project, TaskCreate(name="Work"))
    store.start_task(project, "work")
    return project, "work"


def tool(store: Store, tool_id: str, **fields: str) -> None:
    """A tool file in the root, e.g. tool(store, "wait", command="sleep 60")."""
    lines = [f"{name} = {json.dumps(value)}" for name, value in {"name": tool_id, **fields}.items()]
    (store.root / ".tools").mkdir(exist_ok=True)
    (store.root / ".tools" / f"{tool_id}.toml").write_text("\n".join(lines) + "\n")


def open_pane(workspaces: Workspaces, task: tuple[str, str], tool_id: str, **request):
    return workspaces.open_pane(*task, PaneOpen(tool=tool_id, **request))


def workspace(store: Store, task: tuple[str, str]):
    return store.task(*task).workspace


def output_until(pty: Pty, pattern: bytes) -> bytes:
    async def read() -> bytes:
        return await read_until(pty.listen(), pattern)

    return asyncio.run(read())


def row(*children) -> Split:
    return Split(direction="row", children=list(children))


def column(*children) -> Split:
    return Split(direction="column", children=list(children))


# -- panes ------------------------------------------------------------------


def test_panes_are_numbered_and_tiled(store, workspaces, task):
    first = open_pane(workspaces, task, "terminal")
    second = open_pane(workspaces, task, "terminal")
    third = open_pane(workspaces, task, "terminal", beside="p1")

    assert [first.id, second.id, third.id] == ["p1", "p2", "p3"]
    assert workspace(store, task).tree == row(column("p1", "p3"), "p2")
    assert (first.tool, first.title) == ("terminal", "Terminal")
    assert len({uuid.UUID(pane.session) for pane in (first, second, third)}) == 3

    workspaces.close_pane(*task, "p3")
    assert open_pane(workspaces, task, "terminal").id == "p4"
    assert sorted(Store(store.root).task(*task).workspace.panes) == ["p1", "p2", "p4"]


def test_a_pane_needs_a_started_task_and_a_known_tool(store, workspaces, task):
    project, _ = task
    store.create_task(project, TaskCreate(name="Later"))

    with pytest.raises(NotStarted):
        open_pane(workspaces, (project, "later"), "terminal")
    with pytest.raises(UnknownTool):
        open_pane(workspaces, task, "no-such-tool")

    assert workspace(store, task).panes == {}
    assert workspace(store, (project, "later")).panes == {}


@pytest.mark.parametrize(
    "values", [{}, {"file": "  "}, {"file": "../outside.md"}, {"file": "/etc/hosts"}]
)
def test_a_parameter_needs_a_value_and_a_path_stays_in_the_task_directory(workspaces, task, values):
    with pytest.raises(InvalidValues):
        open_pane(workspaces, task, "vim", values=values)


def test_a_path_is_accepted_when_the_root_is_reached_through_a_link(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    store = Store(tmp_path / "link" / "root")
    project = store.create_project(ProjectMeta(name="Demo")).slug
    store.create_task(project, TaskCreate(name="Work"))
    store.start_task(project, "work")
    workspaces = Workspaces(store, shell=["/bin/sh"])

    pane = open_pane(workspaces, (project, "work"), "vim", values={"file": "notes/a.md"})

    assert pane.values == {"file": "notes/a.md"}


def test_only_a_path_parameter_is_held_to_the_task_directory(store, workspaces, task):
    (store.root / ".tools" / "say.toml").write_text(
        'name = "Say"\ncommand = "echo {text}"\n\n[[parameters]]\nname = "text"\n'
    )

    pane = open_pane(workspaces, task, "say", values={"text": "/etc/hosts"})

    assert pane.values == {"text": "/etc/hosts"}


def test_parameter_values_are_kept_and_shown_in_the_title(workspaces, task):
    pane = open_pane(workspaces, task, "vim", values={"file": " notes/a.md ", "other": "dropped"})

    assert (pane.title, pane.values) == ("Vim: notes/a.md", {"file": "notes/a.md"})


def test_the_user_can_rearrange_the_panes_but_not_change_which_there_are(store, workspaces, task):
    open_pane(workspaces, task, "terminal")
    open_pane(workspaces, task, "terminal")

    rearranged = Split(direction="column", children=["p2", "p1"], split_percentages=[30, 70])
    workspaces.set_tree(*task, rearranged)
    assert Store(store.root).task(*task).workspace.tree == rearranged
    layout_file = Path(store.task(*task).path) / ".bytearmy" / "layout.json"
    assert json.loads(layout_file.read_text())["tree"] == {  # as react-mosaic spells it
        "type": "split",
        "direction": "column",
        "children": ["p2", "p1"],
        "splitPercentages": [30.0, 70.0],
    }

    for broken in ("p1", row("p1", "p2", "p3"), row("p1", "p2", "p1"), None):
        with pytest.raises(InvalidLayout):
            workspaces.set_tree(*task, broken)
    assert workspace(store, task).tree == rearranged


def test_an_unreadable_layout_counts_as_an_empty_workspace(store, workspaces, task):
    open_pane(workspaces, task, "terminal")
    (Path(store.task(*task).path) / ".bytearmy" / "layout.json").write_text("not json")

    assert workspace(store, task).panes == {}
    assert open_pane(workspaces, task, "terminal").id == "p1"


def test_a_task_without_a_layout_is_not_reported_as_unreadable(store, task, caplog):
    assert workspace(store, task).panes == {}
    assert caplog.records == []


def test_a_layout_that_cannot_be_read_counts_as_an_empty_workspace(store, task):
    (Path(store.task(*task).path) / ".bytearmy" / "layout.json").mkdir()

    assert workspace(store, task).panes == {}


def test_one_pane_can_be_maximized(store, workspaces, task):
    for _ in range(3):
        open_pane(workspaces, task, "terminal")

    workspaces.maximize(*task, "p2")
    assert Store(store.root).task(*task).workspace.maximized == "p2"
    with pytest.raises(PaneNotFound):
        workspaces.maximize(*task, "p9")
    assert workspace(store, task).maximized == "p2"

    workspaces.close_pane(*task, "p3")
    assert workspace(store, task).maximized == "p2"
    workspaces.close_pane(*task, "p2")
    assert workspace(store, task).maximized is None

    workspaces.maximize(*task, "p1")
    workspaces.maximize(*task, None)
    assert workspace(store, task).maximized is None


# -- programs ---------------------------------------------------------------


def test_attaching_starts_the_program_of_a_new_pane_at_that_size_in_the_task_directory(
    store, workspaces, task
):
    tool(store, "where", command=f'echo "at $PWD $(stty size)"; {WAIT}')
    open_pane(workspaces, task, "where")

    pty = workspaces.attach(*task, "p1", rows=33, cols=99)

    assert f"at {Path(store.task(*task).path).resolve()} 33 99".encode() in output_until(
        pty, rb"99"
    )
    assert workspaces.attach(*task, "p1", rows=10, cols=10) is pty
    with pytest.raises(PaneNotFound):
        workspaces.attach(*task, "p9", rows=10, cols=10)


def test_a_tool_without_a_command_runs_the_shell_itself(workspaces, task):
    open_pane(workspaces, task, "terminal")
    pty = workspaces.attach(*task, "p1", 24, 80)

    async def scenario():
        output = pty.listen()
        await read_until(output, rb"[$#] ")  # the prompt
        pty.write(b"echo typed-$((40 + 2))\n")
        await read_until(output, rb"typed-42")

    asyncio.run(scenario())


def test_by_default_a_program_runs_in_the_users_interactive_login_shell(
    store, task, tmp_path, monkeypatch
):
    shell = tmp_path / "fake-shell"
    shell.write_text(f'#!/bin/sh\necho "started with: $*."\n{WAIT}\n')
    shell.chmod(0o755)
    monkeypatch.setenv("SHELL", str(shell))
    workspaces = Workspaces(store)
    open_pane(workspaces, task, "terminal")

    try:
        seen = output_until(workspaces.attach(*task, "p1", 24, 80), rb"\.")
    finally:
        workspaces.shutdown()

    assert b"started with: -l -i." in seen


def test_a_program_gets_the_pane_environment(store, workspaces, task, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([f"{sys.prefix}/bin", "/usr/bin", "/bin"]))
    monkeypatch.setenv("PIXI_PROJECT_ROOT", "/core")
    tool(store, "env", command=f'echo "$TERM_PROGRAM ${{PIXI_PROJECT_ROOT:-unset}} $PATH."; {WAIT}')
    open_pane(workspaces, task, "env")

    seen = output_until(workspaces.attach(*task, "p1", 24, 80), rb"\.")

    assert b"bytearmy unset /usr/bin:/bin." in seen


def test_a_tool_can_run_in_the_clone_if_the_task_has_one(store, workspaces):
    tool(store, "here", directory="repo", command=f'echo "in $PWD."; {WAIT}')
    tool(store, "above", command=f'echo "in $PWD."; {WAIT}')
    project = store.create_project(ProjectMeta(name="Widgets", github_url="/srv/widgets.git")).slug
    for name in ("cloned", "not cloned"):
        store.create_task(project, TaskCreate(name=name))
        store.start_task(project, name.replace(" ", "-"))
    cloned = Path(store.task(project, "cloned").path)
    (cloned / "widgets" / ".git").mkdir(parents=True)

    for slug in ("cloned", "not-cloned"):
        open_pane(workspaces, (project, slug), "here")
    open_pane(workspaces, (project, "cloned"), "above")
    in_clone = output_until(workspaces.attach(project, "cloned", "p1", 24, 80), rb"\.")
    in_task = output_until(workspaces.attach(project, "not-cloned", "p1", 24, 80), rb"\.")
    above_clone = output_until(workspaces.attach(project, "cloned", "p2", 24, 80), rb"\.")

    assert f"in {(cloned / 'widgets').resolve()}.".encode() in in_clone
    assert f"in {Path(store.task(project, 'not-cloned').path).resolve()}.".encode() in in_task
    assert f"in {cloned.resolve()}.".encode() in above_clone


def test_a_program_that_ends_cleanly_closes_its_pane(store, workspaces, task):
    tool(store, "ok", command="exit 0")
    open_pane(workspaces, task, "terminal")
    open_pane(workspaces, task, "ok")
    workspaces.maximize(*task, "p2")

    workspaces.attach(*task, "p2", 24, 80)
    wait_until(lambda: "p2" not in workspace(store, task).panes, "closing the pane")

    assert workspace(store, task).tree == "p1"
    assert workspace(store, task).maximized is None


def test_a_program_that_fails_keeps_its_pane_and_its_last_output(store, workspaces, task):
    tool(store, "fail", command="echo broke-$((40 + 2)); exit 3")
    open_pane(workspaces, task, "fail")

    pty = workspaces.attach(*task, "p1", 24, 80)
    wait_until(lambda: workspace(store, task).panes["p1"].exit_code == 3, "recording the failure")

    assert workspace(store, task).tree == "p1"
    again = workspaces.attach(*task, "p1", 24, 80)
    assert again is pty
    assert b"broke-42" in asyncio.run(read_to_end(again.listen()))

    restarted = Workspaces(store, shell=["/bin/sh"])
    restarted.start_all()
    assert restarted.attach(*task, "p1", 24, 80) is None


def test_a_program_that_fails_after_its_pane_is_gone_changes_nothing(store, workspaces, task):
    tool(store, "fail", command="read _; exit 3")
    open_pane(workspaces, task, "fail")
    pty = workspaces.attach(*task, "p1", 24, 80)
    (Path(store.task(*task).path) / ".bytearmy" / "layout.json").write_text("not json")

    pty.write(b"\n")
    wait_until(lambda: pty.exit_code == 3, "the end of the program")

    assert workspace(store, task).panes == {}


@pytest.mark.parametrize("deleted", ["task", "project"])
def test_a_program_that_ends_after_its_task_was_deleted_changes_nothing(
    store, workspaces, task, deleted
):
    tool(store, "fail", command="read _; exit 3")
    open_pane(workspaces, task, "fail")
    pty = workspaces.attach(*task, "p1", 24, 80)
    if deleted == "task":
        store.delete_task(*task)
    else:
        store.delete_project(task[0])

    pty.write(b"\n")
    wait_until(lambda: pty.exit_code == 3, "the end of the program")


def test_closing_a_pane_ends_its_program(store, workspaces, task):
    tool(store, "wait", command=WAIT)
    open_pane(workspaces, task, "wait")
    pty = workspaces.attach(*task, "p1", 24, 80)

    workspaces.close_pane(*task, "p1")

    assert pty.exit_code is not None
    assert workspace(store, task).panes == {}
    assert workspace(store, task).tree is None
    with pytest.raises(PaneNotFound):
        workspaces.close_pane(*task, "p1")


def test_stop_ends_the_programs_of_a_task_and_keeps_its_panes(store, workspaces, task):
    project, _ = task
    store.create_task(project, TaskCreate(name="Other"))
    store.start_task(project, "other")
    elsewhere = store.create_project(ProjectMeta(name="Elsewhere")).slug
    store.create_task(elsewhere, TaskCreate(name="Work"))
    store.start_task(elsewhere, "work")
    tool(store, "wait", command=WAIT)
    for place in ((project, "work"), (project, "other"), (elsewhere, "work")):
        open_pane(workspaces, place, "wait")
    work = workspaces.attach(project, "work", "p1", 24, 80)
    other = workspaces.attach(project, "other", "p1", 24, 80)
    untouched = workspaces.attach(elsewhere, "work", "p1", 24, 80)

    workspaces.stop(project, "work")

    assert (work.exit_code is not None, other.exit_code, untouched.exit_code) == (True, None, None)
    assert workspace(store, task).panes["p1"].exit_code is None
    assert workspaces.attach(project, "work", "p1", 24, 80) is not work  # started again

    workspaces.stop(project)
    assert (other.exit_code is not None, untouched.exit_code) == (True, None)
    assert workspace(store, (project, "other")).panes["p1"].exit_code is None


def test_a_program_started_again_after_a_stop_closes_its_pane_when_it_ends_cleanly(
    store, workspaces, task
):
    tool(store, "once", command="read _; exit 0")
    open_pane(workspaces, task, "once")
    workspaces.attach(*task, "p1", 24, 80)
    workspaces.stop(*task)

    workspaces.attach(*task, "p1", 24, 80).write(b"\n")

    wait_until(lambda: "p1" not in workspace(store, task).panes, "closing the pane")


def test_shutdown_ends_every_program_and_keeps_the_panes_for_the_next_start(store, task):
    tool(store, "wait", command=WAIT)
    workspaces = Workspaces(store, shell=["/bin/sh"])
    open_pane(workspaces, task, "wait")
    pty = workspaces.attach(*task, "p1", 24, 80)

    workspaces.shutdown()

    assert pty.exit_code is not None
    assert workspace(store, task).panes["p1"].exit_code is None
    assert workspace(store, task).tree == "p1"


def test_shutdown_tells_every_program_to_end_before_it_waits_for_one(store, task):
    slow = "trap 'trap \"\" HUP; sleep 1; exit 0' HUP; echo ready; while true; do sleep 0.1; done"
    tool(store, "slow", command=slow)
    workspaces = Workspaces(store, shell=["/bin/sh"])
    for _ in range(3):
        pane = open_pane(workspaces, task, "slow")
        output_until(workspaces.attach(*task, pane.id, 24, 80), rb"ready")

    began = time.monotonic()
    workspaces.shutdown()

    assert time.monotonic() - began < 2  # each takes a second to end: 1 s together, 3 s in turn


def test_a_program_that_ends_after_shutdown_does_not_change_its_pane(store, task):
    tool(store, "wait", command=WAIT)
    workspaces = Workspaces(store, shell=["/bin/sh"])
    open_pane(workspaces, task, "wait")
    workspaces.attach(*task, "p1", 24, 80)
    workspaces.shutdown()

    workspaces._ended((*task, "p1"), 1)  # it took longer to end than shutdown waits

    assert workspace(store, task).panes["p1"].exit_code is None


def test_a_restart_restores_every_pane_with_its_resume_command_and_size(store, task):
    tool(
        store,
        "agent",
        command=f'echo "new {{session}} $(stty size)."; {WAIT}',
        resume=f'echo "resumed {{session}} $(stty size)."; {WAIT}',
    )
    tool(store, "plain", command=f'echo "plain $(stty size)."; {WAIT}')
    first = Workspaces(store, shell=["/bin/sh"])
    agent = open_pane(first, task, "agent")
    open_pane(first, task, "plain")
    assert f"new {agent.session} 30 100.".encode() in output_until(
        first.attach(*task, "p1", 30, 100), rb"\."
    )
    first.attach(*task, "p2", 24, 80)
    first.resized(*task, "p1", 41, 133)
    first.shutdown()

    second = Workspaces(store, shell=["/bin/sh"])
    second.start_all()
    try:
        restored = output_until(second.attach(*task, "p1", 24, 80), rb"\.")
        plain = output_until(second.attach(*task, "p2", 24, 80), rb"\.")
    finally:
        second.shutdown()

    assert f"resumed {agent.session} 41 133.".encode() in restored
    assert b"plain 24 80." in plain


def test_start_all_leaves_a_running_program_alone(store, workspaces, task):
    tool(store, "wait", command=WAIT)
    open_pane(workspaces, task, "wait")
    pty = workspaces.attach(*task, "p1", 24, 80)

    workspaces.start_all()

    assert workspaces.attach(*task, "p1", 24, 80) is pty


def test_the_size_of_a_pane_that_is_gone_is_not_remembered(store, workspaces, task):
    open_pane(workspaces, task, "terminal")

    workspaces.resized(*task, "p9", 10, 10)

    assert sorted(workspace(store, task).panes) == ["p1"]


def test_a_pane_whose_tool_is_gone_is_kept_as_failed(store, task):
    tool(store, "wait", command=WAIT)
    first = Workspaces(store, shell=["/bin/sh"])
    open_pane(first, task, "wait")
    first.shutdown()
    (store.root / ".tools" / "wait.toml").unlink()

    second = Workspaces(store, shell=["/bin/sh"])
    second.start_all()

    assert workspace(store, task).panes["p1"].exit_code == COULD_NOT_START
    assert second.attach(*task, "p1", 24, 80) is None


# -- pages and windows ------------------------------------------------------


def editor(store: Store, fake_service, before: str = "") -> None:
    """A web tool like the IDE: it shows a service in the clone or the task directory."""
    (store.root / ".tools").mkdir(exist_ok=True)
    (store.root / ".tools" / "editor.toml").write_text(
        'name = "Editor"\nkind = "web"\ndirectory = "repo"\n'
        'url = "{service}&folder={directory}"\n\n[service]\n'
        f"command = {json.dumps(fake_service.command(before=before))}\n"
        f"url = {json.dumps(fake_service.url)}\n"
    )


def cloned_and_plain(store: Store) -> tuple[str, Path, Path]:
    """A project with a task that has a clone and one that has none; their directories."""
    project = store.create_project(ProjectMeta(name="Widgets", github_url="/srv/widgets.git")).slug
    for name in ("cloned", "plain"):
        store.create_task(project, TaskCreate(name=name))
        store.start_task(project, name)
    cloned = Path(store.task(project, "cloned").path)
    (cloned / "widgets" / ".git").mkdir(parents=True)
    return project, cloned, Path(store.task(project, "plain").path)


def test_a_web_tool_opens_a_pane_that_shows_a_page(store, workspaces, task):
    pane = open_pane(workspaces, task, "webpage", values={"url": " example.org/docs "})

    assert (pane.kind, pane.title) == ("web", "Webpage: example.org/docs")
    assert Store(store.root).task(*task).workspace.panes["p1"].kind == "web"
    assert workspaces.page(*task, "p1", files="") == "https://example.org/docs"
    assert open_pane(workspaces, task, "terminal").kind == "terminal"

    workspaces.close_pane(*task, "p1")
    assert sorted(workspace(store, task).panes) == ["p2"]


def test_the_webpage_tool_offers_the_page_of_the_project(store, workspaces):
    meta = ProjectMeta(name="Widgets", github_url="git@github.com:me/widgets.git")
    place = (store.create_project(meta).slug, "work")
    store.create_task(place[0], TaskCreate(name="Work"))
    store.start_task(*place)

    offered = open_pane(workspaces, place, "webpage")
    entered = open_pane(workspaces, place, "webpage", values={"url": "https://example.org"})

    assert offered.values == {"url": "https://github.com/me/widgets"}
    assert entered.values == {"url": "https://example.org"}


@pytest.mark.parametrize(
    ("values", "why"),
    [({}, "URL is required."), ({"url": "file:///etc/passwd"}, "file:///etc/passwd is not a web")],
)
def test_a_page_must_be_named_and_be_a_web_page(store, workspaces, task, values, why):
    with pytest.raises(InvalidValues, match=why):
        open_pane(workspaces, task, "webpage", values=values)

    assert workspace(store, task).panes == {}


def test_the_html_tool_shows_a_file_of_the_task(workspaces, task):
    open_pane(workspaces, task, "html", values={"file": "out/my report.html"})

    assert workspaces.page(*task, "p1", files="http://core/files/key/demo/work") == (
        "http://core/files/key/demo/work/out/my%20report.html"
    )
    with pytest.raises(InvalidValues):
        open_pane(workspaces, task, "html", values={"file": "../other/report.html"})


def test_the_version_of_a_page_changes_when_its_file_is_written_again(store, workspaces, task):
    report = Path(store.task(*task).path) / "report.html"
    open_pane(workspaces, task, "html", values={"file": "report.html"})

    def version_after(text: str, seconds: int) -> str | None:
        report.write_text(text)
        os.utime(report, (seconds, seconds))
        return workspaces.page_version(*task, "p1")

    missing = workspaces.page_version(*task, "p1")
    written = version_after("one", seconds=1000)
    longer = version_after("three", seconds=1000)  # another size at the same time
    later = version_after("three", seconds=2000)  # the same size at another time

    assert len({missing, written, longer, later}) == 4
    assert None not in (missing, written, longer, later)
    assert workspaces.page_version(*task, "p1") == later


def test_only_a_web_pane_that_shows_a_file_has_a_version(store, workspaces, task):
    open_pane(workspaces, task, "webpage", values={"url": "example.org"})
    open_pane(workspaces, task, "terminal")
    open_pane(workspaces, task, "html", values={"file": "report.html"})

    assert workspaces.page_version(*task, "p1") is None
    for pane in ("p2", "p9"):
        with pytest.raises(PaneNotFound):
            workspaces.page_version(*task, pane)
    (store.root / ".tools" / "html.toml").write_text(
        'name = "HTML"\nkind = "web"\nurl = "{files}/{file}"\n\n'
        '[[parameters]]\nname = "file"\npath = true\n\n'
        '[[parameters]]\nname = "style"\npath = true\n'
    )
    assert workspaces.page_version(*task, "p3") is not None  # a parameter added later
    (store.root / ".tools" / "html.toml").unlink()
    assert workspaces.page_version(*task, "p3") is None  # its tool is gone


def test_a_tool_with_a_service_shows_it_in_the_clone_or_else_in_the_task_directory(
    store, workspaces, fake_service
):
    editor(store, fake_service, before=f'echo "$TERM_PROGRAM" > {store.root / "seen"}; ')
    project, cloned, plain = cloned_and_plain(store)
    for slug in ("cloned", "plain"):
        open_pane(workspaces, (project, slug), "editor")

    in_clone = workspaces.page(project, "cloned", "p1", files="")
    in_task = workspaces.page(project, "plain", "p1", files="")

    service, folder = in_clone.split("&folder=")
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/\?tkn=[\w-]+", service)
    assert folder == quote(str(cloned / "widgets"))
    assert in_task == f"{service}&folder={quote(str(plain))}"
    assert len(fake_service.started()) == 1  # both panes share the service
    assert (store.root / "seen").read_text() == "bytearmy\n"  # it runs in the pane environment


def test_shutdown_ends_the_services(store, task, fake_service):
    editor(store, fake_service)
    workspaces = Workspaces(store, shell=["/bin/sh"])
    open_pane(workspaces, task, "editor")
    workspaces.page(*task, "p1", files="")
    (pids,) = fake_service.started()

    workspaces.shutdown()

    wait_until(lambda: not any(running(pid) for pid in pids), "the end of the service")


def test_only_a_web_pane_of_a_web_tool_has_a_page(store, workspaces, task):
    open_pane(workspaces, task, "terminal")
    open_pane(workspaces, task, "webpage", values={"url": "example.org"})

    for pane in ("p1", "p9"):
        with pytest.raises(PaneNotFound):
            workspaces.page(*task, pane, files="")
    tool(store, "webpage", command="true")  # the tool file now describes a terminal tool
    with pytest.raises(UnknownTool):
        workspaces.page(*task, "p2", files="")
    (store.root / ".tools" / "webpage.toml").unlink()
    with pytest.raises(UnknownTool):
        workspaces.page(*task, "p2", files="")


def test_a_pane_stored_before_there_were_web_panes_is_a_terminal(store, workspaces, task):
    tool(store, "wait", command=WAIT)
    open_pane(workspaces, task, "wait")
    layout = Path(store.task(*task).path) / ".bytearmy" / "layout.json"
    stored = json.loads(layout.read_text())
    del stored["panes"]["p1"]["kind"]
    layout.write_text(json.dumps(stored))

    assert workspace(store, task).panes["p1"].kind == "terminal"
    assert workspaces.attach(*task, "p1", 24, 80) is not None


def test_a_web_pane_has_no_program(store, task):
    first = Workspaces(store, shell=["/bin/sh"])
    open_pane(first, task, "webpage", values={"url": "example.org"})
    second = Workspaces(store, shell=["/bin/sh"])
    try:
        with pytest.raises(PaneNotFound):
            first.attach(*task, "p1", 24, 80)
        second.start_all()

        assert (first._ptys, second._ptys) == ({}, {})
        assert workspace(store, task).panes["p1"].exit_code is None
    finally:
        first.shutdown()
        second.shutdown()


def test_an_external_tool_runs_its_command_and_gets_no_pane(store, workspaces, task):
    (store.root / ".tools" / "note.toml").write_text(
        'name = "Note"\nkind = "external"\ncommand = "echo $TERM_PROGRAM {text} > noted"\n\n'
        '[[parameters]]\nname = "text"\n'
    )

    assert open_pane(workspaces, task, "note", values={"text": "a; b"}) is None

    assert (Path(store.task(*task).path) / "noted").read_text() == "bytearmy a; b\n"
    assert (workspace(store, task).panes, workspace(store, task).tree) == ({}, None)


def test_an_external_tool_can_start_in_the_clone_if_the_task_has_one(store, workspaces):
    tool(store, "mark", kind="external", directory="repo", command="touch marked")
    project, cloned, plain = cloned_and_plain(store)

    for slug in ("cloned", "plain"):
        open_pane(workspaces, (project, slug), "mark")

    assert (cloned / "widgets" / "marked").exists()
    assert (plain / "marked").exists()
    assert not (cloned / "marked").exists()


def test_an_external_tool_that_fails_is_reported_with_what_it_printed(store, workspaces, task):
    tool(store, "broken", kind="external", command="echo no window >&2; exit 2")

    with pytest.raises(ToolFailed) as failure:
        open_pane(workspaces, task, "broken")

    assert str(failure.value) == "broken failed (exit code 2). no window"
    assert (store.root / ".bytearmy" / "logs" / "broken.log").read_text() == "no window\n"


def test_pane_environment_is_the_users_without_the_cores_own_additions():
    environ = {
        "HOME": "/home/me",
        "PATH": "/core/env/bin:/usr/bin::/core/env/tools:/home/me/bin",
        "PIXI_PROJECT_ROOT": "/core",
        "CONDA_PREFIX": "/core/env",
        "TERM": "dumb",
    }

    assert pane_env(environ, prefix="/core/env") == {
        "HOME": "/home/me",
        "PATH": "/usr/bin:/home/me/bin",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "TERM_PROGRAM": "bytearmy",
    }
