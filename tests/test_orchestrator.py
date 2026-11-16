"""Checks for the orchestrator: the one agent above all tasks."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import read_until, wait_until
from test_api_panes import TOKEN, serving
from test_inbox import FakeGh, thread
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from bytearmy import cli
from bytearmy.agents import BY_ORCHESTRATOR, ORCHESTRATOR_INSTRUCTIONS
from bytearmy.inbox import GitHubSource, Inbox
from bytearmy.models import InboxState, Message
from bytearmy.orchestrator import Orchestrator, describe
from bytearmy.ptys import Pty
from bytearmy.store import Store
from bytearmy.workspaces import Workspaces

WAIT = "sleep 60"
KEEP_ENV = "env | grep ^BYTEARMY_ > orchestrator.env"  # lets a check call as the orchestrator
# Stands in for Claude: says how it was started, then echoes what it is told.
BOT = {
    "name": "Bot",
    "agent": True,
    "command": f"{KEEP_ENV}; printf '%s %s %s.\\n' start {{agent}} {{instructions}}; "
    "while read line; do echo got:$line.; done",
    "resume": f"{KEEP_ENV}; printf '%s %s %s.\\n' resume {{agent}} {{instructions}}; {WAIT}",
    "spawn": f"printf '%s %s %s.\\n' spawn {{agent}} {{prompt}}; {WAIT}",
}


def claude(store: Store, **fields) -> None:
    """Replace the Claude tool of the root, which the orchestrator and the masters run."""
    lines = [f"{name} = {json.dumps(value)}" for name, value in {**BOT, **fields}.items()]
    (store.root / ".tools" / "claude.toml").write_text("\n".join(lines) + "\n")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def workspaces(store):
    made = Workspaces(store, shell=["/bin/sh"])
    claude(store)
    yield made
    made.shutdown()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def orchestrator(store, workspaces, clock):
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True, clock=clock)
    yield made
    made.shutdown()


def output_until(pty: Pty, pattern: bytes) -> str:
    async def read() -> bytes:
        return await read_until(pty.listen(), pattern)

    return asyncio.run(read()).decode()


# -- its program -----------------------------------------------------------------


def test_it_starts_with_bytearmy_and_is_told_who_it_is(store, orchestrator):
    orchestrator.start()

    pty = orchestrator.attach(24, 80)
    said = output_until(pty, rb"start orchestrator \S+\.")
    told = Path(said.split("start orchestrator ")[1].split(".\r")[0].rstrip("."))
    assert told.read_text().startswith(ORCHESTRATOR_INSTRUCTIONS)
    assert "Your name is `orchestrator`." in told.read_text()
    assert (store.root / ".claude" / "orchestrator.md").read_text() == ORCHESTRATOR_INSTRUCTIONS
    assert orchestrator.state().running


def test_the_users_instructions_file_is_kept_and_used(store, workspaces):
    (store.root / ".claude" / "orchestrator.md").write_text("Mine.\n")
    Workspaces(store, shell=["/bin/sh"])
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    try:
        made.start()
        said = output_until(made.attach(24, 80), rb"start orchestrator \S+\.")
        told = Path(said.split("start orchestrator ")[1].split(".\r")[0].rstrip("."))
        assert told.read_text().startswith("Mine.\n")
    finally:
        made.shutdown()


def test_after_a_restart_it_resumes_its_session(store, workspaces):
    first = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    first.start()
    output_until(first.attach(24, 80), b"start orchestrator")
    session = json.loads((store.root / ".bytearmy" / "orchestrator.json").read_text())["session"]
    first.shutdown()

    second = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    try:
        second.start()
        output_until(second.attach(24, 80), b"resume orchestrator")
        saved = json.loads((store.root / ".bytearmy" / "orchestrator.json").read_text())
        assert saved["session"] == session
    finally:
        second.shutdown()


def test_without_autostart_it_starts_when_the_window_shows_it(store, workspaces):
    made = Orchestrator(store, workspaces, shell=["/bin/sh"])
    try:
        made.start()
        assert not made.state().running
        output_until(made.attach(30, 90), b"start orchestrator")
        assert made.state().running
    finally:
        made.shutdown()


def test_an_error_is_kept_until_the_user_starts_it_again(store, workspaces, orchestrator):
    claude(store, command="echo run >> runs; exit 3", resume="echo run >> runs; exit 3")
    orchestrator.start()
    wait_until(lambda: orchestrator.state().exit_code == 3, "the program to fail")
    assert not orchestrator.state().running
    orchestrator.start()  # bytearmy started again: the error stands
    assert (store.root / "runs").read_text() == "run\n"

    orchestrator.restart()
    wait_until(lambda: (store.root / "runs").read_text() == "run\nrun\n", "a second run")


def test_ended_well_it_starts_again_when_next_shown(store, orchestrator):
    claude(store, command="echo run >> runs", resume="echo run >> runs")
    orchestrator.start()
    wait_until(lambda: not orchestrator.state().running, "the program to end")
    assert orchestrator.state().exit_code is None

    orchestrator.attach(24, 80)
    wait_until(lambda: (store.root / "runs").read_text() == "run\nrun\n", "a second run")


# -- notes from bytearmy -----------------------------------------------------------


def test_a_note_is_typed_in_once_the_user_paused(orchestrator, clock):
    orchestrator.start()
    pty = orchestrator.attach(24, 80)
    output_until(pty, b"start orchestrator")
    orchestrator.write(b"\r")  # the user just typed

    orchestrator.notify("hello\nthere")
    clock.now += 31
    orchestrator.notify("again")

    assert "got:hello there again." in output_until(pty, rb"got:hello there again\.")


def test_a_note_waits_while_the_user_types(orchestrator, clock):
    orchestrator.start()
    pty = orchestrator.attach(24, 80)
    output_until(pty, b"start orchestrator")
    orchestrator.write(b"\r")

    orchestrator.notify("later")
    output_until(pty, b"got:.")  # what the user typed arrived
    assert orchestrator._notes == ["later"]


def test_new_messages_are_described_in_one_line():
    messages = [
        Message(id=f"github:{n}", source="github", title=f"T{n}", context="o/r", updated_at="x")
        for n in range(12)
    ]
    note = describe(messages)
    assert "\n" not in note
    assert note.startswith("bytearmy: 12 new messages in the inbox: [github:0] T0 (o/r); ")
    assert "[github:9] T9 (o/r); and 2 more." in note
    assert "github:10" not in note
    assert describe(messages[:1]).startswith("bytearmy: 1 new message in the inbox")


def test_the_inbox_announces_only_what_arrives_after_its_first_check(store):
    gh, heard = FakeGh(), []
    inbox = Inbox(
        store,
        [GitHubSource(run=gh)],
        now=lambda: datetime(2026, 11, 15, tzinfo=UTC),
        on_new=heard.append,
    )
    gh.threads = [thread(1, "2026-11-15T10:00:00Z")]
    inbox.check()
    assert heard == []  # waited while bytearmy did not run: no news

    gh.threads = [thread(2, "2026-11-15T11:00:00Z"), thread(1, "2026-11-15T10:00:00Z")]
    inbox.check()
    inbox.check()
    assert [[message.id for message in batch] for batch in heard] == [["github:2"]]

    inbox.dismiss("github:2")
    gh.threads = [thread(2, "2026-11-15T12:00:00Z")]
    inbox.check()
    assert [message.id for message in heard[-1]] == ["github:2"]  # new activity
    inbox.shutdown()


# -- its `bytearmy` command, against a running core ----------------------------------


@pytest.fixture
def core(store, workspaces):
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    inbox = Inbox(store)
    with serving(store, workspaces, orchestrator=made, inbox=inbox) as live:
        live.orchestrator = made
        live.inbox = inbox
        yield live


def key(store: Store) -> dict[str, str]:
    """The variables the orchestrator's program got, once it has written them down."""
    file = store.root / "orchestrator.env"
    wait_until(lambda: file.exists() and "BYTEARMY_TOKEN" in file.read_text(), "its key")
    return dict(line.split("=", 1) for line in file.read_text().splitlines())


@pytest.fixture
def bytearmy(core, monkeypatch, capsys):
    """Run the command as the orchestrator: (exit code, what it printed)."""
    env = key(core.store)
    monkeypatch.delenv("BYTEARMY_PANE", raising=False)
    for name in ("BYTEARMY_URL", "BYTEARMY_TOKEN"):
        monkeypatch.setenv(name, env[name])

    def run(*args: str) -> tuple[int, str]:
        code = cli.main(list(args))
        out, err = capsys.readouterr()
        return code, (out + err).strip()

    return run


def test_the_orchestrator_creates_starts_and_deletes_projects_and_tasks(core, bytearmy):
    assert bytearmy("project", "list") == (0, "no projects")
    assert bytearmy("project", "create", "Alpha", "--description", "d") == (
        0,
        "created project alpha",
    )
    assert bytearmy("tasks", "create", "alpha", "Prepare") == (
        0,
        "created task alpha/prepare in the backlog",
    )
    assert bytearmy("tasks", "create", "alpha", "Fix bug", "--depends-on", "prepare")[0] == 0

    code, said = bytearmy("tasks", "start", "alpha", "fix-bug", "--prompt", "Fix it")
    assert code == 1 and "waits for" in said
    assert bytearmy("tasks", "start", "alpha", "prepare") == (0, "started alpha/prepare")
    assert bytearmy("tasks", "start", "alpha", "fix-bug", "--prompt", "Fix it") == (
        0,
        "started alpha/fix-bug; its master is alpha-fix-bug-master",
    )

    task = core.store.task("alpha", "fix-bug")
    master = task.workspace.panes[task.workspace.master]
    assert (master.agent, master.spawned_by, master.hidden) == (
        "alpha-fix-bug-master",
        "orchestrator",
        False,
    )
    told = Path(task.path) / ".bytearmy" / "agents" / f"{master.id}.md"
    wait_until(told.exists, "the master's instructions")
    assert BY_ORCHESTRATOR in told.read_text()
    wait_until(lambda: core.workspaces.master_running("alpha", "fix-bug"), "the master")

    code, listed = bytearmy("tasks", "list", "alpha")
    assert listed.splitlines() == [
        "alpha  Alpha",
        "  prepare  Prepare  (New)",
        "  fix-bug  Fix bug  (New, master alpha-fix-bug-master)",
    ]
    assert bytearmy("tasks", "delete", "alpha", "fix-bug") == (0, "deleted task alpha/fix-bug")
    assert not Path(task.path).exists()
    assert bytearmy("project", "delete", "alpha") == (0, "deleted project alpha")
    assert bytearmy("project", "list") == (0, "no projects")


def test_starting_a_task_that_has_a_master_with_a_prompt_is_refused(core, bytearmy):
    bytearmy("project", "create", "Alpha")
    bytearmy("tasks", "create", "alpha", "Work")
    bytearmy("tasks", "start", "alpha", "work", "--prompt", "Go")

    code, said = bytearmy("tasks", "start", "alpha", "work", "--prompt", "Go again")
    assert (code, said) == (1, "bytearmy: this task already has an agent with the role 'master'")


def test_the_orchestrator_reads_the_inbox(core, bytearmy):
    messages = [
        Message(id="github:1", source="github", title="Review me", context="o/r",
                reason="review_requested", url="https://github.com/o/r/pull/1",
                updated_at="2026-11-15T10:00:00Z"),
        Message(id="github:2", source="github", title="Old", context="o/r",
                updated_at="2026-11-14T10:00:00Z", unread=False),
    ]  # fmt: skip
    core.inbox._save(InboxState(messages=messages))

    _code, listed = bytearmy("inbox", "list")
    assert listed.splitlines() == [
        "github:1  Review me  [o/r]  (review_requested, 2026-11-15T10:00:00Z, unread)",
        "  https://github.com/o/r/pull/1",
    ]
    assert len(bytearmy("inbox", "list", "--all")[1].splitlines()) == 3

    assert bytearmy("inbox", "read", "github:1") == (0, "marked github:1 read")
    assert bytearmy("inbox", "list") == (0, "no unread messages")
    assert bytearmy("inbox", "unread", "github:1")[0] == 0
    assert bytearmy("inbox", "dismiss", "github:1") == (0, "dismissed github:1")
    assert bytearmy("inbox", "list", "--all")[1].startswith("github:2")
    assert bytearmy("inbox", "read", "github:9") == (1, "bytearmy: no message 'github:9'")


def test_only_the_orchestrator_reaches_its_routes(core):
    me = key(core.store)
    orchestrator = {"Authorization": f"Bearer {me['BYTEARMY_TOKEN']}"}
    assert core.http.get("/orchestrator/projects", headers=orchestrator).status_code == 200
    # Neither the window's token nor a wrong key reaches them.
    assert core.http.get("/orchestrator/projects").status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert core.http.get("/orchestrator/projects", headers=wrong).status_code == 401
    # The orchestrator's key reaches no task's routes, and not the window's.
    assert core.http.get("/agent/panes", headers=orchestrator).status_code == 401
    assert core.http.get("/api/state", headers=orchestrator).status_code == 401


def test_the_window_shows_the_orchestrators_terminal_and_starts_it_again(core):
    address = core.url.replace("http", "ws", 1)
    with connect(f"{address}/api/orchestrator/terminal?token={TOKEN}&rows=30&cols=90") as socket:
        seen = b""
        while b"start orchestrator" not in seen:
            frame = socket.recv(timeout=5)
            seen += frame if isinstance(frame, bytes) else b""
        socket.send(json.dumps({"type": "input", "data": "hi\r"}))
        while b"got:hi." not in seen:
            frame = socket.recv(timeout=5)
            seen += frame if isinstance(frame, bytes) else b""

    state = core.http.get("/api/state").json()["orchestrator"]
    assert state == {"name": "orchestrator", "running": True, "exit_code": None}
    assert core.http.post("/api/orchestrator/restart").json()["orchestrator"]["running"]
    with pytest.raises(InvalidStatus):
        connect(f"{address}/api/orchestrator/terminal?token=wrong").recv(timeout=5)


def test_the_orchestrator_view_is_part_of_the_ui_state(core):
    changed = core.http.patch("/api/ui", json={"view": "orchestrator"})
    assert changed.json()["ui"]["view"] == "orchestrator"


# -- what config.toml says about it ---------------------------------------------------


def configure(store: Store, text: str) -> None:
    (store.root / ".bytearmy" / "config.toml").write_text(text)


def test_the_config_says_which_commands_the_orchestrator_may_call(store, workspaces):
    configure(store, '[orchestrator]\nallow = ["project.*", "inbox.list"]\n')
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    with serving(store, workspaces, orchestrator=made, inbox=Inbox(store)) as live:
        made.start()
        orchestrator = {"Authorization": f"Bearer {key(store)['BYTEARMY_TOKEN']}"}
        assert live.http.get("/orchestrator/projects", headers=orchestrator).status_code == 200
        live.http.post("/orchestrator/projects", headers=orchestrator, json={"name": "Alpha"})
        refused = live.http.post(
            "/orchestrator/projects/alpha/tasks", headers=orchestrator, json={"name": "Work"}
        )
        assert refused.status_code == 403
        assert "[orchestrator]" in refused.json()["detail"]
        assert live.http.get("/orchestrator/inbox", headers=orchestrator).status_code == 200
        dismissed = live.http.post("/orchestrator/inbox/github:1/dismiss", headers=orchestrator)
        assert dismissed.status_code == 403
        told = (store.root / ".bytearmy" / "agents" / "orchestrator.md").read_text()
        assert "`tasks.create`" in told and "`project.create`" not in told
        made.shutdown()


def test_the_task_switches_keep_the_orchestrator_from_creating_and_deleting(store, workspaces):
    configure(store, "[orchestrator]\nallow_task_creation = false\nallow_task_deletion = false\n")
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    with serving(store, workspaces, orchestrator=made, inbox=Inbox(store)) as live:
        made.start()
        orchestrator = {"Authorization": f"Bearer {key(store)['BYTEARMY_TOKEN']}"}
        live.http.post("/orchestrator/projects", headers=orchestrator, json={"name": "Alpha"})
        refused = live.http.post(
            "/orchestrator/projects/alpha/tasks", headers=orchestrator, json={"name": "Work"}
        )
        assert refused.status_code == 403
        assert "allow_*" in refused.json()["detail"]
        told = (store.root / ".bytearmy" / "agents" / "orchestrator.md").read_text()
        assert "`tasks.create`" in told and "`tasks.delete`" in told
        assert "`tasks.start`" not in told
        made.shutdown()


def test_the_config_can_keep_the_orchestrator_from_starting_with_bytearmy(store, workspaces):
    configure(store, "[orchestrator]\nautostart = false\n")
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    try:
        made.start()
        assert not made.state().running
        assert made.attach(24, 80) is not None  # shown, it starts
    finally:
        made.shutdown()


def test_the_config_gives_the_orchestrator_its_command(store, workspaces):
    configure(
        store,
        "[orchestrator]\n"
        'command = "echo mine {agent} {permissions}.; sleep 60"\n'
        'permission_mode = "acceptEdits"\n',
    )
    made = Orchestrator(store, workspaces, shell=["/bin/sh"], autostart=True)
    try:
        made.start()
        said = output_until(made.attach(24, 80), rb"acceptEdits\.")
        assert "mine orchestrator --permission-mode acceptEdits." in said
    finally:
        made.shutdown()


def test_the_config_can_keep_the_inbox_from_the_orchestrator(store, orchestrator):
    configure(store, "[orchestrator]\nannounce_inbox = false\n")
    orchestrator.announce([Message(id="github:1", source="github", title="t", updated_at="x")])
    assert orchestrator._notes == []
