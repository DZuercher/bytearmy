"""Checks for panes and their terminals over HTTP and WebSocket, against a live server."""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from conftest import wait_until
from fastapi.testclient import TestClient
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from bytearmy.api import create_app
from bytearmy.app import free_port
from bytearmy.models import PaneOpen, ProjectMeta, TaskCreate
from bytearmy.store import Store
from bytearmy.workspaces import Workspaces

TOKEN = "secret"
PANES = "/api/projects/demo/tasks/work/panes"
ECHO = 'while read word; do echo "got-$word$word $(stty size)."; done'


def tool(store: Store, tool_id: str, command: str) -> None:
    (store.root / ".tools" / f"{tool_id}.toml").write_text(
        f"name = {json.dumps(tool_id)}\ncommand = {json.dumps(command)}\n"
    )


def started_task(store: Store) -> None:
    """Project `demo` with the started task `work`."""
    store.create_project(ProjectMeta(name="Demo"))
    store.create_task("demo", TaskCreate(name="Work"))
    store.start_task("demo", "work")


@dataclass
class Live:
    """A running core: its address, the objects behind it and an HTTP client."""

    url: str
    store: Store
    workspaces: Workspaces
    http: httpx.Client

    def terminal(self, pane: str, query: str = f"token={TOKEN}&rows=31&cols=97"):
        address = self.url.replace("http", "ws", 1)
        return connect(f"{address}{PANES}/{pane}/terminal?{query}")

    def workspace(self) -> dict:
        return self.http.get("/api/state").json()["projects"][0]["tasks"][0]["workspace"]


@pytest.fixture
def live(tmp_path):
    store = Store(tmp_path / "root")
    started_task(store)
    with serving(store, Workspaces(store, shell=["/bin/sh"])) as core:
        yield core


@contextmanager
def serving(store: Store, workspaces: Workspaces):
    """Run the core on a local port for the duration of the block."""
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(store, TOKEN, workspaces=workspaces),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_until(lambda: server.started, "starting the server")
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            yield Live(url, store, workspaces, http)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def read_until(socket, pattern: bytes, timeout: float = 5) -> bytes:
    """Collect terminal output until it matches, or fail showing what arrived."""
    seen = b""
    deadline = time.monotonic() + timeout
    while not re.search(pattern, seen):
        try:
            frame = socket.recv(timeout=max(deadline - time.monotonic(), 0))
        except TimeoutError:
            pytest.fail(f"no match for {pattern!r} within {timeout}s; saw {seen!r}")
        if isinstance(frame, bytes):
            seen += frame
    return seen


def send(socket, **message) -> None:
    socket.send(json.dumps(message))


# -- panes over HTTP --------------------------------------------------------


def test_pane_lifecycle_over_http(live):
    opened = live.http.post(PANES, json={"tool": "terminal"})
    assert opened.status_code == 201
    assert opened.json()["projects"][0]["tasks"][0]["workspace"]["tree"] == "p1"
    assert [tool["id"] for tool in opened.json()["tools"]] == [
        "claude",
        "explorer",
        "html",
        "ide",
        "terminal",
        "vim",
        "webpage",
    ]

    live.http.post(PANES, json={"tool": "vim", "values": {"file": "a.md"}, "beside": "p1"})
    assert live.workspace()["tree"]["children"] == ["p1", "p2"]
    assert live.workspace()["panes"]["p2"]["title"] == "Vim: a.md"

    layout = {
        "type": "split",
        "direction": "column",
        "children": ["p2", "p1"],
        "splitPercentages": [30, 70],
    }
    arranged = live.http.put("/api/projects/demo/tasks/work/layout", json={"tree": layout})
    assert arranged.json()["projects"][0]["tasks"][0]["workspace"]["tree"] == layout

    live.http.put("/api/projects/demo/tasks/work/maximized", json={"slug": "p2"})
    assert live.workspace()["maximized"] == "p2"

    live.http.delete(f"{PANES}/p2")
    assert (live.workspace()["tree"], live.workspace()["maximized"]) == ("p1", None)


def test_pane_errors_map_to_status_codes(live):
    live.store.create_task("demo", TaskCreate(name="Later"))
    live.http.post(PANES, json={"tool": "terminal"})
    task = "/api/projects/demo/tasks/work"

    assert live.http.post(PANES, json={"tool": "ghost"}).status_code == 404
    assert (
        live.http.post(
            "/api/projects/demo/tasks/ghost/panes", json={"tool": "terminal"}
        ).status_code
        == 404
    )
    assert live.http.delete(f"{PANES}/p9").status_code == 404
    assert live.http.put(f"{task}/maximized", json={"slug": "p9"}).status_code == 404
    assert live.http.put(f"{task}/layout", json={"tree": None}).status_code == 400

    in_backlog = live.http.post("/api/projects/demo/tasks/later/panes", json={"tool": "terminal"})
    assert (in_backlog.status_code, in_backlog.json()["detail"]) == (
        409,
        '"Later" is still in the backlog.',
    )
    no_file = live.http.post(PANES, json={"tool": "vim"})
    assert (no_file.status_code, no_file.json()["detail"]) == (
        400,
        "File, relative to the task directory is required.",
    )
    assert live.workspace()["tree"] == "p1"


# -- pages, files and windows -----------------------------------------------


def page(live: Live, pane: str) -> httpx.Response:
    return live.http.get(f"{PANES}/{pane}/page")


def test_a_web_pane_names_its_page(live):
    live.http.post(PANES, json={"tool": "webpage", "values": {"url": "example.org/docs"}})
    live.http.post(PANES, json={"tool": "terminal"})

    assert live.workspace()["panes"]["p1"]["kind"] == "web"
    assert (page(live, "p1").status_code, page(live, "p1").json()) == (
        200,
        {"url": "https://example.org/docs"},
    )
    assert [page(live, pane).status_code for pane in ("p2", "p9")] == [404, 404]


def test_the_state_offers_the_defaults_of_the_focused_project(live):
    def offered(state: httpx.Response) -> str:
        (webpage,) = [tool for tool in state.json()["tools"] if tool["id"] == "webpage"]
        return webpage["parameters"][0]["default"]

    widgets = {"name": "Widgets", "github_url": "git@github.com:me/widgets.git"}

    assert offered(live.http.post("/api/projects", json=widgets)) == "https://github.com/me/widgets"
    assert offered(live.http.put("/api/focus", json={"slug": "demo"})) == ""
    assert offered(live.http.put("/api/focus", json={"slug": None})) == ""


def test_the_page_of_an_html_pane_is_a_file_of_the_task_served_without_the_token(live):
    task_dir = live.store.root / "demo" / "work"
    (task_dir / "out").mkdir()
    (task_dir / "out" / "my report.html").write_text("<h1>report</h1>")
    live.http.post(PANES, json={"tool": "html", "values": {"file": "out/my report.html"}})

    url = page(live, "p1").json()["url"]
    served = httpx.get(url)

    assert re.fullmatch(rf"{live.url}/files/[\w-]{{16,}}/demo/work/out/my%20report\.html", url)
    assert (served.status_code, served.text) == (200, "<h1>report</h1>")
    assert served.headers["content-type"].startswith("text/html")


def test_a_web_pane_can_ask_whether_its_file_changed(live):
    report = live.store.root / "demo" / "work" / "report.html"
    report.write_text("one")
    live.http.post(PANES, json={"tool": "html", "values": {"file": "report.html"}})
    live.http.post(PANES, json={"tool": "webpage", "values": {"url": "example.org"}})

    def version(pane: str) -> httpx.Response:
        return live.http.get(f"{PANES}/{pane}/page/version")

    before = version("p1").json()["version"]
    report.write_text("another text")

    assert isinstance(before, str)
    assert version("p1").json()["version"] != before
    assert (version("p2").status_code, version("p2").json()) == (200, {"version": None})
    assert version("p9").status_code == 404


def test_only_files_inside_the_task_directory_are_served_and_only_with_the_key(live, tmp_path):
    task_dir = live.store.root / "demo" / "work"
    (task_dir / "page.html").write_text("inside")
    (tmp_path / "outside.txt").write_text("outside")
    (task_dir / "link.txt").symlink_to(tmp_path / "outside.txt")
    live.http.post(PANES, json={"tool": "html", "values": {"file": "page.html"}})
    files = page(live, "p1").json()["url"].removesuffix("/page.html")
    key = files.split("/")[4]

    def status(url: str) -> int:
        return httpx.get(url).status_code

    assert status(f"{files}/page.html") == 200
    assert status(f"{files}/missing.html") == 404
    assert status(f"{files}/") == 404  # a directory
    assert status(f"{files}/link.txt") == 404  # leads out of the task directory
    assert (live.store.root / ".bytearmy" / "state.json").is_file()
    assert status(f"{files}/%2e%2e/%2e%2e/.bytearmy/state.json") == 404
    assert status(f"{live.url}/files/{key}/demo/ghost/page.html") == 404
    assert status(f"{live.url}/files/{'x' * len(key)}/demo/work/page.html") == 404
    assert status(f"{live.url}/files/{TOKEN}/demo/work/page.html") == 404


def test_files_are_served_when_the_root_is_reached_through_a_link(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    store = Store(tmp_path / "link" / "root")
    started_task(store)
    (store.root / "demo" / "work" / "page.html").write_text("inside")
    workspaces = Workspaces(store, shell=["/bin/sh"])
    workspaces.open_pane("demo", "work", PaneOpen(tool="html", values={"file": "page.html"}))

    with serving(store, workspaces) as core:
        served = httpx.get(core.http.get(f"{PANES}/p1/page").json()["url"])

    assert (served.status_code, served.text) == (200, "inside")


def test_an_external_tool_opens_no_pane_and_its_failure_is_reported(live):
    tools = live.store.root / ".tools"
    (tools / "window.toml").write_text('name = "Window"\nkind = "external"\ncommand = "touch w"\n')
    (tools / "broken.toml").write_text(
        'name = "Broken"\nkind = "external"\ncommand = "echo no window >&2; exit 2"\n'
    )

    opened = live.http.post(PANES, json={"tool": "window"})
    broken = live.http.post(PANES, json={"tool": "broken"})

    assert opened.status_code == 201
    assert (live.store.root / "demo" / "work" / "w").exists()
    assert (broken.status_code, broken.json()["detail"]) == (
        502,
        "Broken failed (exit code 2). no window",
    )
    assert live.workspace()["panes"] == {}


# -- terminals --------------------------------------------------------------


@pytest.mark.parametrize(
    ("place", "query"),
    [
        ("projects/demo/tasks/work/panes/p1", "rows=24&cols=80"),
        ("projects/demo/tasks/work/panes/p1", "token=wrong"),
        ("projects/demo/tasks/work/panes/p9", f"token={TOKEN}"),
        ("projects/demo/tasks/ghost/panes/p1", f"token={TOKEN}"),
        ("projects/ghost/tasks/work/panes/p1", f"token={TOKEN}"),
        ("projects/demo/tasks/work/panes/p1", f"token={TOKEN}&rows=x"),
    ],
)
def test_a_terminal_needs_the_token_and_an_existing_pane(live, place, query):
    live.http.post(PANES, json={"tool": "terminal"})
    address = live.url.replace("http", "ws", 1)

    with (
        pytest.raises(InvalidStatus) as refused,
        connect(f"{address}/api/{place}/terminal?{query}"),
    ):
        pass

    assert refused.value.response.status_code == 403  # refused, not crashed
    assert live.workspaces._ptys == {}  # nothing was started


def test_a_terminal_carries_input_output_and_resizes(live):
    tool(live.store, "echo", ECHO)
    live.http.post(PANES, json={"tool": "echo"})

    with live.terminal("p1") as socket:
        send(socket, type="input", data="ab\n")
        read_until(socket, rb"got-abab 31 97\.")
        send(socket, type="resize", rows=20, cols=50)
        send(socket, type="input", data="cd\n")
        read_until(socket, rb"got-cdcd 20 50\.")

    wait_until(lambda: live.workspace()["panes"]["p1"]["rows"] == 20, "storing the size")
    assert live.workspace()["panes"]["p1"]["cols"] == 50


def test_two_terminals_can_watch_the_same_pane(live):
    tool(live.store, "echo", ECHO)
    live.http.post(PANES, json={"tool": "echo"})

    with live.terminal("p1", f"token={TOKEN}") as first, live.terminal("p1") as second:
        send(first, type="input", data="ab\n")
        read_until(first, rb"got-abab 24 80\.")  # the size of a terminal that names none
        read_until(second, rb"got-abab")
    assert len(live.workspaces._ptys) == 1
    (pty,) = live.workspaces._ptys.values()
    wait_until(lambda: not pty._listeners, "forgetting the closed terminals")


def test_the_end_of_a_program_is_announced_after_its_pane_was_updated(live):
    tool(live.store, "leave", 'read code; exit "$code"')
    live.http.post(PANES, json={"tool": "leave"})
    live.http.post(PANES, json={"tool": "leave"})

    with live.terminal("p1") as clean, live.terminal("p2") as failing:
        send(clean, type="input", data="0\n")
        send(failing, type="input", data="3\n")
        for socket in (clean, failing):
            while (frame := socket.recv(timeout=5)) != '{"type": "exit"}':
                assert isinstance(frame, bytes), frame
        workspace = live.workspace()  # the announcement comes after the update

    assert list(workspace["panes"]) == ["p2"]
    assert workspace["panes"]["p2"]["exit_code"] == 3
    assert workspace["tree"] == "p2"


def test_a_pane_that_failed_in_an_earlier_run_only_announces_its_end(tmp_path):
    store = Store(tmp_path / "root")
    started_task(store)
    earlier = Workspaces(store, shell=["/bin/sh"])
    tool(store, "fail", "exit 3")
    earlier.open_pane("demo", "work", PaneOpen(tool="fail"))
    earlier.attach("demo", "work", "p1", 24, 80)
    wait_until(lambda: store.task("demo", "work").workspace.panes["p1"].exit_code == 3, "failing")

    app = create_app(store, TOKEN, workspaces=Workspaces(store, shell=["/bin/sh"]))
    with (
        TestClient(app) as client,
        client.websocket_connect(f"{PANES}/p1/terminal?token={TOKEN}") as socket,
    ):
        assert socket.receive_text() == '{"type": "exit"}'


# -- programs and the life of the app ---------------------------------------


@pytest.mark.parametrize("target", ["/api/projects/demo/tasks/work", "/api/projects/demo"])
def test_deleting_a_task_or_its_project_ends_its_programs(live, target):
    tool(live.store, "wait", "sleep 60")
    live.http.post(PANES, json={"tool": "wait"})
    pty = live.workspaces.attach("demo", "work", "p1", 24, 80)

    assert live.http.delete(target).status_code == 200

    assert pty.exit_code is not None
    assert not (live.store.root / "demo" / "work").exists()


def test_the_app_restores_the_panes_when_it_starts_and_ends_them_when_it_stops(tmp_path):
    store = Store(tmp_path / "root")
    started_task(store)
    earlier = Workspaces(store, shell=["/bin/sh"])
    (store.root / ".tools" / "agent.toml").write_text(
        'name = "Agent"\ncommand = "echo new; sleep 60"\nresume = "echo resumed; sleep 60"\n'
    )
    earlier.open_pane("demo", "work", PaneOpen(tool="agent"))

    with serving(store, Workspaces(store, shell=["/bin/sh"])) as core:
        pty = core.workspaces.attach("demo", "work", "p1", 24, 80)
        with core.terminal("p1") as socket:
            read_until(socket, rb"resumed")

    assert pty.exit_code is not None
    assert store.task("demo", "work").workspace.panes["p1"].exit_code is None
