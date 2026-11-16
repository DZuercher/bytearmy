"""Checks for agents and for the `bytearmy` command the programs in the panes call."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from conftest import read_until, wait_until
from fastapi.testclient import TestClient
from test_api_panes import TOKEN, serving, started_task
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from bytearmy import cli
from bytearmy.agents import HAS_MASTER, INSTRUCTIONS, IS_MASTER, NO_MASTER, SPAWNED
from bytearmy.api import create_app
from bytearmy.models import PaneOpen, ProjectMeta, Split, TaskCreate
from bytearmy.ptys import Pty
from bytearmy.store import Store
from bytearmy.workspaces import InvalidValues, RoleTaken, Workspaces, values_from_args

WAIT = "sleep 60"
KEEP_ENV = 'env | grep ^BYTEARMY_ > "$BYTEARMY_PANE.env"'  # lets a check call as the pane
# An agent that says how it was started.
BOT = {
    "name": "Bot",
    "agent": True,
    "command": f"{KEEP_ENV}; printf '%s %s %s.\\n' start {{agent}} {{instructions}}; {WAIT}",
    "resume": f"{KEEP_ENV}; printf '%s %s %s.\\n' resume {{agent}} {{instructions}}; {WAIT}",
    "spawn": f"{KEEP_ENV}; printf '%s %s %s.\\n' spawn {{agent}} {{prompt}}; {WAIT}",
}


def tool(store: Store, tool_id: str, **fields) -> None:
    lines = [f"{name} = {json.dumps(value)}" for name, value in fields.items()]
    (store.root / ".tools" / f"{tool_id}.toml").write_text("\n".join(lines) + "\n")


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
    project = store.create_project(ProjectMeta(name="Demo")).slug
    store.create_task(project, TaskCreate(name="Work"))
    store.start_task(project, "work")
    return project, "work"


def open_pane(workspaces: Workspaces, task: tuple[str, str], tool_id: str, **extra):
    return workspaces.open_pane(*task, PaneOpen(tool=tool_id), **extra)


def workspace(store: Store, task: tuple[str, str] = ("demo", "work")):
    return store.task(*task).workspace


def output_until(pty: Pty, pattern: bytes) -> str:
    async def read() -> bytes:
        return await read_until(pty.listen(), pattern)

    return asyncio.run(read()).decode()


def told(store: Store, task: tuple[str, str], pane: str) -> str:
    """The instructions the agent of a pane started with."""
    return (Path(store.task(*task).path) / ".bytearmy" / "agents" / f"{pane}.md").read_text()


# -- names and the master ------------------------------------------------------


def test_the_first_agent_of_a_task_is_its_master(store, workspaces, task):
    tool(store, "bot", **BOT)

    opened = [open_pane(workspaces, task, "bot") for _ in range(4)]

    assert [(pane.agent, pane.title) for pane in opened] == [
        ("demo-work-master", "Bot: master"),
        ("demo-work-agent-2", "Bot: agent-2"),
        ("demo-work-agent-3", "Bot: agent-3"),
        ("demo-work-agent-4", "Bot: agent-4"),
    ]
    assert workspace(store).master == "p1"
    assert open_pane(workspaces, task, "terminal").agent == ""

    workspaces.close_pane(*task, "p2")
    assert open_pane(workspaces, task, "bot").agent == "demo-work-agent-2"

    workspaces.close_pane(*task, "p1")
    assert workspace(store).master is None
    successor = open_pane(workspaces, task, "bot")
    assert (successor.agent, workspace(store).master) == ("demo-work-master", successor.id)


def test_two_agents_of_a_task_cannot_have_the_same_role(store, workspaces, task):
    tool(store, "bot", **BOT)
    open_pane(workspaces, task, "bot", role="reviewer")
    open_pane(workspaces, task, "bot")

    for role in ("reviewer", "master"):
        with pytest.raises(RoleTaken):
            open_pane(workspaces, task, "bot", role=role)

    assert sorted(pane.agent for pane in workspace(store).panes.values()) == [
        "demo-work-master",
        "demo-work-reviewer",
    ]


def test_every_agent_is_told_who_it_is_and_who_its_master_is(store, workspaces, task):
    assert (store.root / ".claude" / "bytearmy.md").read_text() == INSTRUCTIONS
    (store.root / ".claude" / "bytearmy.md").write_text("Steer bytearmy like this.\n")
    tool(store, "bot", **BOT)
    # While the task has no master: an agent the user opened, and one that was spawned.
    open_pane(workspaces, task, "bot", role="early")
    open_pane(workspaces, task, "bot", role="x", prompt="Do x.", spawned_by="demo-work-early")
    workspaces.start(*task, "p1")
    workspaces.start(*task, "p2")
    open_pane(workspaces, task, "bot")
    open_pane(workspaces, task, "bot")
    open_pane(workspaces, task, "bot", role="a", prompt="Do a.", spawned_by="demo-work-agent-2")
    open_pane(workspaces, task, "bot", role="b", prompt="Do b.", spawned_by="demo-work-master")
    for pane in ("p3", "p4", "p5", "p6"):
        workspaces.start(*task, pane)

    texts = {pane: told(store, task, pane) for pane in ("p1", "p2", "p3", "p4", "p5", "p6")}

    assert all(text.startswith("Steer bytearmy like this.\n") for text in texts.values())
    assert 'Your name is `demo-work-early`, your pane is p1, your task is "Work".' in texts["p1"]
    assert {pane: relations(text) for pane, text in texts.items()} == {
        "p1": {"no master"},
        "p2": {"spawned by demo-work-early"},
        "p3": {"is master"},
        "p4": {"master demo-work-master"},
        "p5": {"spawned by demo-work-agent-2", "master demo-work-master"},
        "p6": {"spawned by demo-work-master"},
    }

    (store.root / ".claude" / "bytearmy.md").unlink()  # deleted while bytearmy runs
    open_pane(workspaces, task, "bot")
    workspaces.start(*task, "p7")
    assert told(store, task, "p7").startswith(INSTRUCTIONS)


def relations(text: str) -> set[str]:
    """What the instructions say about the agent's master and who spawned it."""
    about = text.split("## You", 1)[1]
    found = {"no master"} if NO_MASTER in about else set()
    found |= {"is master"} if IS_MASTER in about else set()
    master = re.escape(HAS_MASTER).replace(re.escape("{master}"), "(.+)")
    found |= {f"master {name.strip('`')}" for name in re.findall(master, about)}
    spawner = re.escape(SPAWNED.splitlines()[0]).replace(re.escape("{spawner}"), "(.+?)")
    found |= {f"spawned by {name.strip('`')}" for name in re.findall(spawner, about)}
    return found


def test_an_agent_starts_with_its_name_its_instructions_and_its_prompt(store, workspaces, task):
    tool(store, "bot", **BOT)
    open_pane(workspaces, task, "bot")
    prompt = "Check $HOME; it's 'quoted'."
    open_pane(
        workspaces, task, "bot", role="reviewer", prompt=prompt, spawned_by="demo-work-master"
    )
    files = Path(store.task(*task).path) / ".bytearmy" / "agents"

    first = output_until(workspaces.attach(*task, "p1", 24, 80), rb"\.\r\n")
    second = output_until(workspaces.attach(*task, "p2", 24, 80), rb"\.\.\r\n")

    assert f"start demo-work-master {files / 'p1.md'}." in first
    assert f"spawn demo-work-reviewer {prompt}." in second

    workspaces.shutdown()
    restarted = Workspaces(store, shell=["/bin/sh"])
    restarted.start_all()
    try:
        again = output_until(restarted.attach(*task, "p2", 24, 80), rb"md\.\r\n")
        assert f"resume demo-work-reviewer {files / 'p2.md'}." in again
    finally:
        restarted.shutdown()


def test_an_agent_pane_from_before_agents_had_names_gets_one(store, workspaces, task):
    tool(store, "bot", **{name: value for name, value in BOT.items() if name != "agent"})
    open_pane(workspaces, task, "bot")
    assert workspace(store).panes["p1"].agent == ""
    tool(store, "bot", **BOT)  # the tool file now says it starts an agent

    seen = output_until(workspaces.attach(*task, "p1", 24, 80), rb"\.\r\n")

    assert "start demo-work-master " in seen
    pane = workspace(store).panes["p1"]
    assert (pane.agent, pane.title, workspace(store).master) == (
        "demo-work-master",
        "Bot: master",
        "p1",
    )


# -- the key of a pane ---------------------------------------------------------


def test_a_program_calls_the_core_as_its_pane_while_it_runs(store, workspaces, task):
    workspaces.url = "http://127.0.0.1:1234"
    show = 'echo "$BYTEARMY_URL $BYTEARMY_TASK $BYTEARMY_PANE $BYTEARMY_TOKEN."; read _'
    tool(store, "show", name="Show", command=show)
    open_pane(workspaces, task, "show")
    open_pane(workspaces, task, "show")
    first = workspaces.attach(*task, "p1", 24, 80)
    second = workspaces.attach(*task, "p2", 24, 80)

    url, place, pane, key = output_until(first, rb"\.\r\n").split()[-4:]
    other = output_until(second, rb"\.\r\n").split()[-1]

    assert (url, place, pane) == ("http://127.0.0.1:1234", "demo/work", "p1")
    assert workspaces.caller(key.rstrip(".")) == ("demo", "work", "p1")
    assert workspaces.caller(other.rstrip(".")) == ("demo", "work", "p2")
    assert workspaces.caller("made-up") is None

    first.write(b"\n")  # the program ends
    wait_until(lambda: workspaces.caller(key.rstrip(".")) is None, "forgetting the key")
    workspaces.close_pane(*task, "p2")
    assert workspaces.caller(other.rstrip(".")) is None


def test_starting_a_pane_twice_starts_its_program_once(store, workspaces, task):
    tool(store, "wait", name="Wait", command=WAIT)
    open_pane(workspaces, task, "wait")
    workspaces.open_pane(*task, PaneOpen(tool="webpage", values={"url": "example.org"}))

    workspaces.start(*task, "p1")
    first = workspaces.attach(*task, "p1", 24, 80)
    workspaces.start(*task, "p1")
    workspaces.start(*task, "p2")  # a web pane has no program

    assert workspaces.attach(*task, "p1", 24, 80) is first
    assert (*task, "p2") not in workspaces._ptys


def test_values_are_given_in_order_or_by_name():
    from bytearmy.models import Parameter, Tool

    two = Tool(id="two", name="Two", parameters=[Parameter(name="a"), Parameter(name="b")])

    assert values_from_args(two, ["1", "2"]) == {"a": "1", "b": "2"}
    assert values_from_args(two, ["b=2", "1"]) == {"a": "1", "b": "2"}
    assert values_from_args(two, ["x=1"]) == {"a": "x=1"}
    assert values_from_args(two, ["a=1", "a=2"]) == {"a": "1", "b": "a=2"}
    assert values_from_args(two, ["b"]) == {"a": "b"}
    with pytest.raises(InvalidValues, match="two takes a, b; got 1 2 3"):
        values_from_args(two, ["1", "2", "3"])


# -- the `bytearmy` command, against a running core ------------------------------


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "root")
    started_task(store)
    store.create_task("demo", TaskCreate(name="Other"))
    store.start_task("demo", "other")
    with serving(store, Workspaces(store, shell=["/bin/sh"])) as live:
        yield live


def key_of(live, pane: str, task: str = "work") -> dict[str, str]:
    """The variables a pane's program got, once it has written them down."""
    file = Path(live.store.task("demo", task).path) / f"{pane}.env"
    wait_until(lambda: file.exists() and "BYTEARMY_TOKEN" in file.read_text(), "the pane's key")
    return dict(line.split("=", 1) for line in file.read_text().splitlines())


def calling_pane(live, task: str = "work") -> dict[str, str]:
    """Start a pane whose program only waits; its variables let a check call as it."""
    tool(live.store, "caller", name="Caller", command=f"{KEEP_ENV}; {WAIT}")
    pane = live.workspaces.open_pane("demo", task, PaneOpen(tool="caller"))
    live.workspaces.start("demo", task, pane.id)
    return key_of(live, pane.id, task)


def row(*children) -> Split:
    return Split(direction="row", children=list(children))


def column(*children) -> Split:
    return Split(direction="column", children=list(children))


def test_a_pane_opens_closes_and_arranges_the_panes_of_its_task(core, bytearmy):
    me = calling_pane(core)

    assert bytearmy(me, "pane", "open", "terminal") == (0, "opened p2")
    assert bytearmy(me, "pane", "open", "webpage", "https://example.org/?a=b") == (0, "opened p3")
    assert workspace(core.store).tree == row(column("p1", "p3"), "p2")
    assert workspace(core.store).panes["p3"].values == {"url": "https://example.org/?a=b"}
    assert bytearmy(me, "pane", "open", "terminal", "--beside", "p2") == (0, "opened p4")
    assert workspace(core.store).tree == row(column("p1", "p3"), column("p2", "p4"))

    assert bytearmy(me, "pane", "maximize", "p3") == (0, "maximized p3")
    assert workspace(core.store).maximized == "p3"
    assert bytearmy(me, "pane", "highlight", "p2") == (0, "highlighted p2")
    assert workspace(core.store).panes["p2"].highlighted
    assert bytearmy(me, "pane", "list") == (
        0,
        (
            "p1  Caller  (you)\np2  Terminal  (highlighted)\n"
            "p3  Webpage: https://example.org/?a=b  (maximized)\np4  Terminal"
        ),
    )
    assert bytearmy(me, "pane", "restore")[0] == 0
    assert bytearmy(me, "pane", "unhighlight", "p2")[0] == 0
    assert workspace(core.store).maximized is None
    assert not workspace(core.store).panes["p2"].highlighted

    assert bytearmy(me, "pane", "move", "p4", "p1") == (0, "moved p4 next to p1")
    assert workspace(core.store).tree == row(column(row("p1", "p4"), "p3"), "p2")
    assert bytearmy(me, "pane", "move", "p4", "p4") == (
        1,
        "bytearmy: a pane cannot be placed next to itself",
    )
    assert bytearmy(me, "pane", "move", "p4", "p9") == (1, "bytearmy: no pane 'p9'")
    assert bytearmy(me, "pane", "move", "p9", "p4") == (1, "bytearmy: no pane 'p9'")
    assert workspace(core.store).tree == row(column(row("p1", "p4"), "p3"), "p2")
    assert ("demo", "work", "p3") not in core.workspaces._ptys  # a page has no program
    assert bytearmy(me, "pane", "close", "p3") == (0, "closed p3")
    assert sorted(workspace(core.store).panes) == ["p1", "p2", "p4"]
    assert bytearmy(me, "pane", "close", "p9") == (1, "bytearmy: no pane 'p9'")
    assert bytearmy(me, "pane", "open", "nothing") == (1, "bytearmy: no tool 'nothing'")


def test_a_pane_hides_and_shows_the_panes_of_its_task(core, bytearmy):
    me = calling_pane(core)
    assert bytearmy(me, "pane", "open", "terminal") == (0, "opened p2")

    assert bytearmy(me, "pane", "open", "terminal", "--hidden") == (0, "opened p3")
    assert ("demo", "work", "p3") in core.workspaces._ptys  # its program starts all the same
    assert workspace(core.store).tree == row("p1", "p2")
    assert bytearmy(me, "pane", "list") == (
        0,
        "p1  Caller  (you)\np2  Terminal\np3  Terminal  (hidden)",
    )

    assert bytearmy(me, "pane", "show", "p3") == (0, "showed p3")  # beside the calling pane
    assert workspace(core.store).tree == row(column("p1", "p3"), "p2")
    assert bytearmy(me, "pane", "hide", "p3") == (0, "hid p3")
    assert bytearmy(me, "pane", "show", "p3", "--beside", "p2") == (0, "showed p3")
    assert workspace(core.store).tree == row("p1", column("p2", "p3"))

    assert bytearmy(me, "pane", "hide", "p2")[0] == 0
    assert bytearmy(me, "pane", "maximize", "p2") == (
        1,
        "bytearmy: p2 is hidden; show it before maximizing it",
    )
    assert bytearmy(me, "pane", "move", "p2", "p1") == (1, "bytearmy: p2 is hidden; show it first")
    assert bytearmy(me, "pane", "highlight", "p2") == (0, "highlighted p2")
    assert workspace(core.store).tree == row(column("p1", "p2"), "p3")
    assert bytearmy(me, "pane", "hide", "p9") == (1, "bytearmy: no pane 'p9'")


def test_a_pane_may_open_only_the_tools_for_agents(core, bytearmy):
    tool(core.store, "mine", name="Mine", callers=["user"])
    me = calling_pane(core)

    assert bytearmy(me, "pane", "open", "html", "report.html") == (0, "opened p2")
    tool(core.store, "window", name="Window", kind="external", command="touch window.txt")
    assert bytearmy(me, "pane", "open", "window") == (0, "started window in its own window")
    assert (Path(core.store.task("demo", "work").path) / "window.txt").exists()
    assert bytearmy(me, "pane", "open", "mine") == (
        1,
        "bytearmy: only the user may open the tool 'mine'",
    )
    code, listed = bytearmy(me, "tool", "list")
    assert code == 0
    assert "html  HTML, web  takes: file" in listed.splitlines()
    assert "mine" not in listed


def test_a_pane_changes_the_data_of_its_task(core, bytearmy, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # nothing answers there
    me = calling_pane(core)

    code, shown = bytearmy(me, "task", "needs-input", "Busy")
    assert code == 0
    assert "status: Busy\nstate: needs_input" in shown
    assert bytearmy(me, "task", "set-status", "Needs review")[0] == 0
    assert bytearmy(me, "task", "set-description", "Fix it")[0] == 0
    assert bytearmy(me, "task", "set-reminder", "2026-12-01")[0] == 0
    task = core.store.task("demo", "work")
    assert (task.status, task.state, task.description, str(task.reminder)) == (
        "Needs review",
        "needs_input",
        "Fix it",
        "2026-12-01",
    )

    # Without a status, a state command sets the state's name as the status.
    for command, state, status in [
        ("working", "working", "Working"),
        ("waiting", "waiting", "Waiting"),
        ("done", "done", "Done"),
    ]:
        assert bytearmy(me, "task", command)[0] == 0
        task = core.store.task("demo", "work")
        assert (task.state, task.status) == (state, status)
    # The project has no GitHub URL: its tasks are never merged.
    assert bytearmy(me, "task", "merged") == (
        1,
        "bytearmy: only a task of a project with a GitHub URL can be merged",
    )
    assert bytearmy(me, "task", "set-reminder", "none")[0] == 0
    task = core.store.task("demo", "work")
    assert (task.status, task.state, task.reminder) == ("Done", "done", None)
    assert bytearmy(me, "task", "set-status", "Backlog") == (
        1,
        "bytearmy: a started task cannot go back to the backlog",
    )
    assert bytearmy(me, "task", "set-status", " ") == (1, "bytearmy: a status must not be empty")
    pink = httpx.patch(
        f"{me['BYTEARMY_URL']}/agent/task",
        json={"state": "manual"},  # only the user sets a task to manual
        headers={"Authorization": f"Bearer {me['BYTEARMY_TOKEN']}"},
        trust_env=False,
    )
    assert pink.status_code == 422
    assert core.store.task("demo", "other").status == "New"


def test_a_pane_reaches_only_its_own_task(core, bytearmy):
    here = calling_pane(core)
    there = calling_pane(core, "other")
    bytearmy(here, "pane", "open", "terminal")

    assert bytearmy(there, "pane", "close", "p2") == (1, "bytearmy: no pane 'p2'")
    assert sorted(workspace(core.store).panes) == ["p1", "p2"]
    assert bytearmy(there, "task", "show")[1].startswith("name: Other\n")


def test_a_project_tool_file_starts_the_agents_of_that_project(core, bytearmy):
    tool(core.store, "claude", **{**BOT, "spawn": f"{KEEP_ENV}; echo root spawned.; {WAIT}"})
    project_tools = core.store.root / "demo" / ".tools"
    project_tools.mkdir()
    (project_tools / "claude.toml").write_text(
        (core.store.root / ".tools" / "claude.toml").read_text().replace("root", "project")
    )
    me = calling_pane(core)

    assert bytearmy(me, "agent", "spawn", "reviewer", "Review.")[0] == 0
    assert "project spawned." in output_until(
        core.workspaces.attach("demo", "work", "p2", 24, 80), rb"spawned\.\r\n"
    )


def test_a_pane_spawns_agents_and_lists_them(core, bytearmy):
    tool(core.store, "bot", **BOT)
    claude = {**BOT, "spawn": f"{KEEP_ENV}; printf 'claude spawned %s.\\n' {{agent}}; {WAIT}"}
    tool(core.store, "claude", **claude)
    me = calling_pane(core)

    spawned = bytearmy(me, "agent", "spawn", "reviewer", "Review the diff.")
    assert spawned == (0, "spawned demo-work-reviewer in pane p2")
    # A pane that is no agent spawns with the Claude tool; the agent starts at once.
    assert key_of(core, "p2")["BYTEARMY_PANE"] == "p2"
    assert "claude spawned demo-work-reviewer." in output_until(
        core.workspaces.attach("demo", "work", "p2", 24, 80), rb"reviewer\.\r\n"
    )
    assert workspace(core.store).tree == "p1"  # a spawned agent starts hidden
    assert workspace(core.store).panes["p2"].hidden

    assert bytearmy(me, "pane", "open", "bot") == (0, "opened p3")
    master = key_of(core, "p3")
    assert bytearmy(master, "agent", "spawn", "helper", "Help.") == (
        0,
        "spawned demo-work-helper in pane p4",
    )
    assert workspace(core.store).tree == row("p1", "p3")
    assert workspace(core.store).panes["p4"].hidden
    helper = workspace(core.store).panes["p4"]
    assert (helper.tool, helper.prompt, helper.spawned_by) == ("bot", "Help.", "demo-work-master")
    assert workspace(core.store).panes["p2"].spawned_by == ""

    assert bytearmy(master, "agent", "list") == (
        0,
        (
            "demo-work-reviewer  pane p2\n"
            "demo-work-master  pane p3  (master, you)\n"
            "demo-work-helper  pane p4"
        ),
    )
    assert bytearmy(master, "agent", "spawn", "helper", "Again.") == (
        1,
        "bytearmy: this task already has an agent with the role 'helper'",
    )
    for role in ("Bad Role", "r" * 33):
        assert bytearmy(master, "agent", "spawn", role, "x")[1].startswith(
            "bytearmy: a role is up to 32 lower-case letters"
        )
    assert bytearmy(master, "agent", "spawn", "r" * 32, "x")[0] == 0
    assert bytearmy(master, "agent", "spawn", "idle", " \n") == (
        1,
        "bytearmy: a prompt must be provided",
    )
    refused = (1, "bytearmy: give the prompt, or --prompt-file, but not both")
    assert bytearmy(master, "agent", "spawn", "nothing") == refused
    assert bytearmy(master, "agent", "spawn", "both", "x", "--prompt-file", "brief.md") == refused


def test_an_agent_that_ended_with_an_error_is_listed_as_not_running(core, bytearmy):
    tool(core.store, "broken", name="Broken", agent=True, command="exit 3")
    me = calling_pane(core)
    bytearmy(me, "pane", "open", "broken")

    wait_until(lambda: workspace(core.store).panes["p2"].exit_code == 3, "the end of the agent")
    core.workspaces.open_pane("demo", "work", PaneOpen(tool="broken"))  # not started yet

    assert bytearmy(me, "agent", "list") == (
        0,
        "demo-work-master  pane p2  (master, not running)\ndemo-work-agent-2  pane p3  (not running)",
    )


def test_an_agent_reads_its_prompt_from_a_file(core, bytearmy, tmp_path):
    tool(core.store, "claude", **BOT)
    me = calling_pane(core)
    (tmp_path / "brief.md").write_text("Line one.\nLine two.")

    assert (
        bytearmy(me, "agent", "spawn", "writer", "--prompt-file", str(tmp_path / "brief.md"))[0]
        == 0
    )
    assert workspace(core.store).panes["p2"].prompt == "Line one.\nLine two."
    code, said = bytearmy(me, "agent", "spawn", "reader", "--prompt-file", str(tmp_path / "no"))
    assert (code, said.split(":")[0]) == (1, "bytearmy")
    assert "No such file or directory" in said


def test_an_agent_whose_tool_cannot_spawn_spawns_with_the_claude_tool(core, bytearmy):
    tool(core.store, "claude", **{**BOT, "name": "Claude"})
    tool(core.store, "plain", **{name: value for name, value in BOT.items() if name != "spawn"})
    core.workspaces.open_pane("demo", "work", PaneOpen(tool="plain"))
    core.workspaces.start("demo", "work", "p1")

    assert bytearmy(key_of(core, "p1"), "agent", "spawn", "x", "Do x.")[0] == 0
    assert workspace(core.store).panes["p2"].tool == "claude"


@pytest.mark.parametrize("claude", ["missing", "without a spawn line"])
def test_spawning_needs_a_tool_that_starts_agents(core, bytearmy, claude):
    file = core.store.root / ".tools" / "claude.toml"
    if claude == "missing":
        file.unlink()
    else:
        tool(
            core.store, "claude", **{name: value for name, value in BOT.items() if name != "spawn"}
        )
    me = calling_pane(core)

    assert bytearmy(me, "agent", "spawn", "reviewer", "Review.") == (
        1,
        (
            "bytearmy: no tool starts agents: .tools/claude.toml needs `agent = true` and a"
            " `spawn` line"
        ),
    )


def test_every_agent_route_needs_the_key_of_a_running_pane(tmp_path):
    app = create_app(Store(tmp_path / "root"), token=TOKEN)
    routes = [
        (method.upper(), path.replace("{pane}", "p1"))
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/agent/")
        for method in operations
    ]
    assert len(routes) == 20

    with TestClient(app) as client:
        for key in ("", TOKEN, "made-up"):
            for method, path in routes:
                answer = client.request(method, path, headers={"Authorization": f"Bearer {key}"})
                assert answer.status_code == 401, (key, method, path)


def test_the_command_is_on_the_path_of_every_pane(core):
    run = "bytearmy pane open terminal > opened.txt 2>&1; echo done.; sleep 60"
    tool(core.store, "runner", name="Runner", command=run)
    # A file in the directory the command is called from cannot replace a module.
    decoy = Path(core.store.task("demo", "work").path) / "json.py"
    decoy.write_text("raise SystemExit('the json.py of the task was loaded')\n")
    core.workspaces.open_pane("demo", "work", PaneOpen(tool="runner"))

    output_until(core.workspaces.attach("demo", "work", "p1", 24, 80), rb"done\.")

    assert (Path(core.store.task("demo", "work").path) / "opened.txt").read_text() == "opened p2\n"


def test_outside_a_pane_the_command_says_so(monkeypatch, capsys):
    monkeypatch.delenv("BYTEARMY_URL", raising=False)
    monkeypatch.delenv("BYTEARMY_TOKEN", raising=False)

    assert cli.main(["pane", "list"]) == 1
    assert (
        capsys.readouterr().err == "bytearmy: this command works only inside a pane of bytearmy\n"
    )


# -- the window hears of changes -----------------------------------------------


def test_the_window_hears_of_every_change(core):
    address = core.url.replace("http", "ws", 1)
    with connect(f"{address}/api/changes?token={TOKEN}") as socket:
        before = int(socket.recv(timeout=5))
        core.http.patch("/api/ui", json={"left_open": False})
        assert int(socket.recv(timeout=5)) > before

    with pytest.raises(InvalidStatus) as refused, connect(f"{address}/api/changes?token=wrong"):
        pass
    assert refused.value.response.status_code == 403


def test_every_change_counts(store, task):
    project, _ = task
    store.create_task(project, TaskCreate(name="Second"))
    from bytearmy.models import TaskChange, UiChange

    changes = {
        "ui": lambda: store.update_ui(UiChange(left_open=False)),
        "project file": lambda: store.reorder_tasks(project, "backlog", ["second"]),
        "task file": lambda: store.change_task(project, "work", TaskChange(status="Busy")),
        "layout": lambda: store.change_workspace(project, "work", lambda workspace: None),
        "clone started": lambda: store.clone_started(project, "work"),
        "task deleted": lambda: store.delete_task(project, "second"),
        "project deleted": lambda: store.delete_project(project),
    }
    for what, change in changes.items():
        before = store.version
        change()
        assert store.version > before, what


def test_the_command_loads_none_of_the_cores_libraries():
    """It runs often, so it must start fast: the standard library only."""
    probe = (
        "import sys, bytearmy.cli; print([m for m in ('fastapi', 'pydantic') if m in sys.modules])"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
    )
    assert done.stdout == "[]\n"


# -- starting a task at once ---------------------------------------------------


def test_a_task_created_to_start_at_once_starts_its_master_with_the_description(store):
    store.create_project(ProjectMeta(name="Demo"))
    workspaces = Workspaces(store, shell=["/bin/sh"])
    tool(store, "claude", **{**BOT, "directory": "repo"})
    app = create_app(store, TOKEN, workspaces=workspaces)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        created = client.post(
            "/api/projects/demo/tasks?start=true",
            json={"name": "Work", "description": "Fix the 'login' bug."},
        )
        assert created.status_code == 201
        found = store.task("demo", "work")
        assert found.status == "New"
        assert created.json()["projects"][0]["focused_task"] == "work"
        assert found.workspace.panes["p1"].agent == "demo-work-master"
        output = output_until(workspaces.attach("demo", "work", "p1", 24, 80), rb"\.\r\n")
        assert "spawn demo-work-master Fix the 'login' bug.." in output

        client.post("/api/projects/demo/tasks", json={"name": "Later"})
        assert store.task("demo", "later").workspace.panes == {}
    workspaces.shutdown()


def test_a_cloned_task_started_at_once_starts_its_master_in_the_clone(store, remote):
    store.create_project(ProjectMeta(name="Widgets", github_url=str(remote)))
    workspaces = Workspaces(store, shell=["/bin/sh"])
    tool(store, "claude", **{**BOT, "directory": "repo", "spawn": f"{KEEP_ENV}; pwd; {WAIT}"})
    app = create_app(store, TOKEN, workspaces=workspaces)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        client.post(
            "/api/projects/widgets/tasks?start=true", json={"name": "A", "description": "Go."}
        )
        wait_until(lambda: "p1" in store.task("widgets", "a").workspace.panes, "the master", 20)
        found = store.task("widgets", "a")
        assert found.clone == "ready"
        output = output_until(workspaces.attach("widgets", "a", "p1", 24, 80), rb"widgets\r\n")
        assert str(Path(found.path) / "widgets") in output
    workspaces.shutdown()


def test_a_task_waiting_for_one_in_the_backlog_is_not_created_to_start(store):
    store.create_project(ProjectMeta(name="Demo"))
    store.create_task("demo", TaskCreate(name="First"))
    app = create_app(store, TOKEN, workspaces=Workspaces(store, shell=["/bin/sh"]))
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        refused = client.post(
            "/api/projects/demo/tasks?start=true", json={"name": "Then", "dependencies": ["first"]}
        )
        assert refused.status_code == 409
        assert [task.slug for task in store.project("demo").backlog] == ["first"]


def test_an_agent_pane_trusts_its_directory_for_claude(core, claude_config):
    tool(core.store, "claude", **BOT)
    tool(core.store, "plain", name="Plain", command=WAIT)
    core.workspaces.open_pane("demo", "work", PaneOpen(tool="plain"))
    core.workspaces.start("demo", "work", "p1")
    assert not claude_config.exists()  # a pane that is no agent trusts nothing

    core.workspaces.open_pane("demo", "work", PaneOpen(tool="claude"))
    core.workspaces.start("demo", "work", "p2")

    directory = str(Path(core.store.task("demo", "work").path).resolve())
    projects = json.loads(claude_config.read_text())["projects"]
    assert projects == {directory: {"hasTrustDialogAccepted": True}}


# -- what config.toml allows --------------------------------------------------------


def configure(store: Store, text: str, directory: Path | None = None) -> None:
    file = (directory or store.root) / ".bytearmy" / "config.toml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def test_the_config_says_which_commands_an_agent_may_call(core, bytearmy):
    tool(core.store, "bot", **BOT)
    configure(core.store, '[master]\nallow = ["pane.*", "task.show"]\n')
    me = calling_pane(core)
    assert bytearmy(me, "pane", "open", "bot") == (0, "opened p2")
    master = key_of(core, "p2")

    assert bytearmy(master, "pane", "list")[0] == 0
    assert bytearmy(master, "agent", "spawn", "helper", "Help.") == (
        1,
        (
            "bytearmy: `bytearmy agent spawn` is not allowed to you: `allow` in [master] of "
            "config.toml lacks it, or an `allow_*` switch there is off. Ask the user."
        ),
    )
    assert bytearmy(master, "task", "set-status", "Busy")[0] == 1
    assert bytearmy(master, "log", "hi")[0] == 1
    assert core.store.task("demo", "work").status == "New"
    told_master = told(core.store, ("demo", "work"), "p2")
    assert "## Not allowed by the config" in told_master and "`agent.spawn`" in told_master
    # A pane without an agent is the user's: the config does not bind it.
    assert bytearmy(me, "task", "set-status", "Busy")[0] == 0

    # A project's config overrides the root's.
    configure(core.store, '[master]\nallow = ["*"]\n', Path(core.store.project("demo").path))
    assert bytearmy(master, "task", "set-status", "Working")[0] == 0


def test_the_config_gives_agents_their_command_and_claudes_permissions(core, bytearmy):
    tool(core.store, "bot", **{**BOT, "command": f"{KEEP_ENV}; echo {{permissions}}.; {WAIT}"})
    configure(
        core.store,
        "[master]\n"
        'permission_mode = "plan"\n'
        'allowed_tools = ["Bash(git *)"]\n'
        "[agents]\n"
        f"spawn = '{KEEP_ENV}; echo configured {{agent}} {{permissions}}.; {WAIT}'\n"
        'disallowed_tools = ["WebFetch"]\n',
    )
    me = calling_pane(core)
    bytearmy(me, "pane", "open", "bot")
    said = output_until(core.workspaces.attach("demo", "work", "p2", 24, 80), rb"Bash\(git \*\)")
    assert "--permission-mode plan --allowedTools=Bash(git *)" in said

    master = key_of(core, "p2")
    assert bytearmy(master, "agent", "spawn", "helper", "Help.")[0] == 0
    said = output_until(core.workspaces.attach("demo", "work", "p3", 24, 80), rb"WebFetch\.")
    assert "configured demo-work-helper --allowedTools=Bash(bytearmy pr *)" in said
    assert "--disallowedTools=WebFetch." in said


def test_the_pull_request_switches_keep_gh_from_the_master(core, bytearmy):
    tool(core.store, "bot", **{**BOT, "command": f"{KEEP_ENV}; echo {{permissions}}.; {WAIT}"})
    configure(
        core.store,
        '[master]\ndisallowed_tools = ["WebFetch"]\nallow_pr_creation = false\n'
        "allow_pr_merge = false\n",
    )
    me = calling_pane(core)
    bytearmy(me, "pane", "open", "bot")
    said = output_until(core.workspaces.attach("demo", "work", "p2", 24, 80), rb"merge \*\)")
    assert (
        "--disallowedTools=WebFetch --disallowedTools=Bash(gh pr create *)"
        " --disallowedTools=Bash(gh pr merge *)"
    ) in said
    told_master = told(core.store, ("demo", "work"), "p2")
    assert "You may not create pull requests" in told_master
    assert "You may not merge pull requests" in told_master


def test_a_tool_the_config_disables_is_not_offered(core, bytearmy):
    tool(core.store, "bot", **BOT)
    me = calling_pane(core)
    configure(core.store, "[tools.bot]\nenabled = false\n")
    assert "bot" not in {listed.id for listed in core.workspaces.tools("demo", "work")}
    assert bytearmy(me, "pane", "open", "bot")[0] == 1


# -- event listeners -------------------------------------------------------------

# An agent that writes down what is typed into it.
LISTENING_BOT = {
    "name": "Bot",
    "agent": True,
    "command": f'{KEEP_ENV}; cat -v > "$BYTEARMY_PANE.got"',
}


def typed(live, pane: str) -> str:
    file = Path(live.store.task("demo", "work").path) / f"{pane}.got"
    return file.read_text() if file.exists() else ""


def two_agents(live) -> tuple[dict[str, str], dict[str, str]]:
    """A master in p1 and a second agent in p2, both writing down what they are told."""
    tool(live.store, "bot", **LISTENING_BOT)
    for _ in range(2):
        pane = live.workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
        live.workspaces.start("demo", "work", pane.id)
    return key_of(live, "p1"), key_of(live, "p2")


def test_an_event_listener_tells_the_agent_that_opened_it(core, bytearmy):
    _, helper = two_agents(core)
    command = "printf 'checks: \\033[1mfailed\\033[0m\\r\\n'; exit 8"

    code, said = bytearmy(helper, "listen", "CI of PR #12", command)
    assert (code, said.split(";")[0]) == (0, "listening in hidden pane p3")
    wait_until(lambda: "^[[201~" in typed(core, "p2"), "the agent is told")

    message = typed(core, "p2")
    assert "The event listener in pane p3 ended with exit code 8." in message
    assert f"Its command was: {command}" in message
    assert message.startswith("^[[200~Event: CI of PR #12.")
    assert "The last lines it printed:\n\nchecks: failed\n^[[201~" in message
    assert "Event:" not in typed(core, "p1")  # not the master: it did not ask
    # Its message tells how it ended, so the pane is gone even after an error.
    wait_until(lambda: "p3" not in workspace(core.store).panes, "the listener's pane closes")
    assert core.store.log("demo", "work")[-1].text == (
        "CI of PR #12: happened (exit code 8); told demo-work-agent-2"
    )


def test_an_event_listener_tells_the_master_when_its_opener_is_gone(core, bytearmy):
    _, helper = two_agents(core)
    assert bytearmy(helper, "listen", "the build", "read line")[0] == 0  # waits for a line
    core.workspaces.close_pane("demo", "work", "p2")

    core.workspaces.attach("demo", "work", "p3", 24, 80).write(b"done\n")

    wait_until(lambda: "^[[200~Event: the build." in typed(core, "p1"), "the master is told")
    assert "exit code 0" in typed(core, "p1")


def test_closing_an_event_listener_tells_nobody(core, bytearmy):
    master, _ = two_agents(core)
    assert bytearmy(master, "listen", "forever", "sleep 60")[0] == 0
    assert workspace(core.store).panes["p3"].hidden

    core.workspaces.close_pane("demo", "work", "p3")

    assert "p3" not in workspace(core.store).panes
    assert "Event:" not in typed(core, "p1")


def test_only_agents_open_event_listeners(core):
    listener = next(tool for tool in core.workspaces.tools() if tool.id == "event_listener")
    assert (listener.callers, listener.notify) == (["agent"], True)
