"""Checks for the spike core. They run against a real server on a local port."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
from websockets.sync.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

TOKEN = "test-token"


@dataclass
class Server:
    url: str
    core: core.Core

    def ws_url(self, path: str) -> str:
        return f"{self.url.replace('http', 'ws', 1)}{path}?token={TOKEN}"

    def post(self, body: dict, token: str | None = TOKEN, timeout: float = 60) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.url}/rpc", data=json.dumps(body).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def rpc(self, method: str, timeout: float = 60, **params):
        status, body = self.post({"method": method, "params": params}, timeout=timeout)
        assert status == 200, body
        return body["result"]


@pytest.fixture
def server(tmp_path):
    port = core.free_port()
    url = f"http://127.0.0.1:{port}"
    spike_core = core.Core(
        root=tmp_path / "root", mode="test", token=TOKEN, url=url, terminal_argv=["/bin/sh"]
    )
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(core.create_app(spike_core), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uvicorn_server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield Server(url=url, core=spike_core)
    uvicorn_server.should_exit = True
    thread.join(timeout=10)


def send_input(ws, text: str) -> None:
    ws.send(json.dumps({"type": "input", "data": text}))


def read_until(ws, pattern: bytes, timeout: float = 5.0) -> bytes:
    """Collect terminal output until it matches, or fail showing what arrived."""
    received = b""
    deadline = time.monotonic() + timeout
    while not re.search(pattern, received):
        remaining = deadline - time.monotonic()
        try:
            frame = ws.recv(timeout=max(remaining, 0))
        except TimeoutError:
            pytest.fail(f"no match for {pattern!r} within {timeout}s; received {received!r}")
        if isinstance(frame, bytes):
            received += frame
    return received


def test_child_output_arrives_on_websocket(server):
    pane = server.rpc("pane.open", tool="terminal")
    with connect(server.ws_url(f"/pty/{pane['id']}")) as ws:
        # The shell echoes the typed text; only its own output contains "live-42".
        send_input(ws, "echo live-$((40 + 2))\n")
        read_until(ws, rb"live-42")


def test_resize_changes_what_stty_reports(server):
    pane = server.rpc("pane.open", tool="terminal")
    with connect(server.ws_url(f"/pty/{pane['id']}")) as ws:
        send_input(ws, "stty size\n")
        read_until(ws, rb"\b24 80\b")
        ws.send(json.dumps({"type": "resize", "rows": 30, "cols": 100}))
        send_input(ws, "stty size\n")
        read_until(ws, rb"\b30 100\b")


def test_pane_open_emits_event_with_pane_spec(server):
    with connect(server.ws_url("/events")) as events:
        pane = server.rpc("pane.open", tool="webpage", arg="https://example.org/x")
        event = json.loads(events.recv(timeout=5))
    assert event == {
        "type": "pane.opened",
        "pane": {
            "id": pane["id"],
            "kind": "web",
            "tool": "webpage",
            "title": "example.org",
            "url": "https://example.org/x",
        },
    }


def test_rpc_without_token_is_rejected(server):
    call = {"method": "pane.list", "params": {}}
    assert server.post(call, token=None)[0] == 401
    assert server.post(call, token="wrong")[0] == 401
    assert server.post(call)[0] == 200


@pytest.mark.skipif(core.find_vscode() is None, reason="VS Code is not installed")
def test_serve_web_answers_with_its_token(server):
    # The first run downloads the VS Code server, hence the long timeout.
    pane = server.rpc("pane.open", timeout=330, tool="ide")
    # Like a browser: the token moves into a cookie on a redirect.
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    with browser.open(pane["url"], timeout=30) as response:
        assert response.status == 200
    assert core._http_status(pane["url"].split("?")[0]) == 403
