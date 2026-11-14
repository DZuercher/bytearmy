"""Throwaway spike core for bytearmy.

One local HTTP + WebSocket server that owns the terminals (PTYs) and the list
of panes, serves the built UI and starts Electron pointing at itself.

Usage: python spikes/core.py <a|b|c>
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import secrets
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

SPIKES = Path(__file__).resolve().parent
REPO = SPIKES.parent
UI_DIR = SPIKES / "ui"
DEFAULT_ROOT = SPIKES / "sandbox-root"
PROJECT = "demo-project"
TASK = "demo-task"
PROJECT_GITHUB_URL = "https://github.com/microsoft/vscode"
REPLAY_BYTES = 1_000_000
INITIAL_ROWS, INITIAL_COLS = 24, 80
SERVE_WEB_STARTUP_SECONDS = 300
VSCODE_CANDIDATES = (
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "/usr/share/code/bin/code",
    "/usr/bin/code",
)

INSTRUCTIONS = """\
# bytearmy

You are running inside a pane of bytearmy, a desktop app that shows several
tool panes side by side for one task. You can steer the app with the `bytearmy`
command, which is on PATH:

- `bytearmy pane open webpage <url>`: show a web page in a new pane
- `bytearmy pane open terminal`: open a shell in a new pane
- `bytearmy pane list`: list the panes with their ids
- `bytearmy pane maximize <id>` and `bytearmy pane restore`
- `bytearmy pane close <id>`
- `bytearmy task set-status "<text>" [--color amber]`: change the task's status tag

Your own pane id is in $BYTEARMY_PANE. The metadata of the project this task
belongs to, including its GitHub URL, is in `{project_json}`.
"""

CHECKLISTS = {
    "a": """\
Spike A: terminal pane. Check in the window:
  1. The prompt behaves like your normal zsh.
  2. `claude` shows your profile picker, then Claude Code renders correctly.
  3. Resizing the window reflows Claude.
  4. `echo $BYTEARMY_PANE; which bytearmy` print values.
  5. `claude --session-id {uuid}`, quit, then
     `claude --resume {uuid}` reopens the same conversation.
""",
    "b": """\
Spike B: web panes in a tiling layout. Check in the window:
  1. github.com renders and scrolls.
  2. VS Code opens the folder and typing works.
  3. Drag the VS Code pane by its title bar to another edge (or press Move):
     unsaved text is still there, the terminal keeps its content.
  4. Maximize and restore (the square button): same.
  5. Press Shift twice with the cursor inside VS Code: the palette appears
     above all panes, Esc closes it.
""",
    "c": """\
Spike C: control loop. Check in the window:
  1. In the terminal pane: `bytearmy pane open webpage https://github.com`
     makes a web pane appear.
  2. `bytearmy pane list`, `bytearmy pane maximize <id>`, `bytearmy pane restore`
     and `bytearmy pane close <id>` work.
  3. Press Shift twice, pick Claude, and ask it:
     "open this project's GitHub page in a pane".
  4. Ask that Claude: "which marker words do you see in your instructions?"
     PINEAPPLE = <root>/CLAUDE.md loaded, WALRUS = <root>/<project>/.claude/CLAUDE.md loaded.
""",
    "d": """\
Spike D: two Claude panes message each other. Pick a profile in both panes, then:
  1. In the master pane ask: "Which other sessions can you message?"
     It names demo-task-helper.
  2. In the master pane ask: "Ask demo-task-helper for a fruit name and tell me its answer."
     The helper pane starts working by itself and the master reports the fruit.
     You type nothing in the helper pane.
  3. Busy receiver: tell the helper "run sleep 5 six times, one command each". While it
     runs, tell the master "send demo-task-helper the word KIWI". The helper shows the
     message before its sleeps are done.
  4. Note any approval dialog that appears in either pane, and what it asked.
""",
}

AGENT_NAMES = ("demo-task-master", "demo-task-helper")

AGENT_NOTE = """
## Other agents in this task

Your session name is `{name}`. Another Claude session works on the same task
under the name `{peers}`. You can message it with the SendMessage tool, and it
can message you. ListAgents shows the sessions you can reach.
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_vscode() -> str | None:
    for candidate in (*VSCODE_CANDIDATES, shutil.which("code")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _make_controlling_tty() -> None:
    # Runs in the child after setsid(): stdin is the PTY slave by then.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Pty:
    """A child process attached to a pseudo-terminal, readable by many sockets."""

    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str], on_exit) -> None:
        self.master_fd, slave_fd = os.openpty()
        _set_winsize(self.master_fd, INITIAL_ROWS, INITIAL_COLS)
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                preexec_fn=_make_controlling_tty,
            )
        finally:
            os.close(slave_fd)
        self.replay = bytearray()
        self.subscribers: set[asyncio.Queue] = set()
        self.closed = False
        self._on_exit = on_exit
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(self.master_fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self.master_fd, 65536)
        except OSError:  # Linux reports the child's exit as EIO
            data = b""
        if not data:
            self._close()
            return
        self.replay += data
        del self.replay[: max(0, len(self.replay) - REPLAY_BYTES)]
        for queue in self.subscribers:
            queue.put_nowait(data)

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._loop.remove_reader(self.master_fd)
        os.close(self.master_fd)
        for queue in self.subscribers:
            queue.put_nowait(None)
        self._on_exit()

    def subscribe(self) -> asyncio.Queue:
        """Queue of output chunks, starting with everything printed so far."""
        queue: asyncio.Queue = asyncio.Queue()
        if self.replay:
            queue.put_nowait(bytes(self.replay))
        if self.closed:
            queue.put_nowait(None)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def write(self, data: bytes) -> None:
        while data and not self.closed:
            data = data[os.write(self.master_fd, data) :]

    def resize(self, rows: int, cols: int) -> None:
        if not self.closed:
            _set_winsize(self.master_fd, rows, cols)

    def kill(self) -> None:
        # SIGHUP, like a closing terminal window, so the child can save its state.
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        self._close()


class Core:
    """All state of the spike: panes, terminals, task status, VS Code server."""

    def __init__(
        self,
        root: Path,
        mode: str,
        token: str,
        url: str,
        terminal_argv: list[str] | None = None,
    ) -> None:
        self.root = root
        self.mode = mode
        self.token = token
        self.url = url
        self.task_dir = root / PROJECT / TASK
        shell = os.environ.get("SHELL", "/bin/sh")
        self.shell = shell
        self.terminal_argv = terminal_argv or [shell, "-l", "-i"]
        self.panes: dict[str, dict] = {}
        self.ptys: dict[str, Pty] = {}
        self.maximized: str | None = None
        self.status = {"status": "New", "color": None}
        self.event_queues: set[asyncio.Queue] = set()
        self.vscode: subprocess.Popen | None = None
        self.vscode_url: str | None = None
        self._vscode_lock = asyncio.Lock()
        self._pane_counter = 0

    # -- sandbox root ------------------------------------------------------

    def prepare_root(self) -> None:
        """Create a miniature bytearmy root with one project and one task."""
        bin_dir = self.root / ".bytearmy" / "bin"
        project_dir = self.root / PROJECT
        for directory in (
            bin_dir,
            self.root / ".claude",
            project_dir / ".bytearmy",
            project_dir / ".claude",
            self.task_dir / ".bytearmy",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        shim = bin_dir / "bytearmy"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{SPIKES / "cli.py"}" "$@"\n'
        )
        shim.chmod(0o755)

        project_json = project_dir / ".bytearmy" / "project.json"
        project_json.write_text(
            json.dumps({"name": PROJECT, "github_url": PROJECT_GITHUB_URL}, indent=2)
        )
        self.instructions.write_text(INSTRUCTIONS.format(project_json=project_json))
        (self.root / "CLAUDE.md").write_text(
            "Marker word PINEAPPLE: this line comes from the bytearmy root CLAUDE.md.\n"
        )
        (project_dir / ".claude" / "CLAUDE.md").write_text(
            "Marker word WALRUS: this line comes from the project's .claude/CLAUDE.md.\n"
        )
        notes = self.task_dir / "notes.md"
        if not notes.exists():
            notes.write_text("# Demo task\n\nScratch file for the spikes.\n")

    @property
    def instructions(self) -> Path:
        return self.root / ".claude" / "bytearmy.md"

    def agent_instructions(self, name: str) -> Path:
        """The general instructions plus who this agent is and who its peers are."""
        peers = ", ".join(peer for peer in AGENT_NAMES if peer != name)
        path = self.task_dir / ".bytearmy" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.instructions.read_text() + AGENT_NOTE.format(name=name, peers=peers))
        return path

    def pane_env(self, pane_id: str) -> dict[str, str]:
        """The user's environment without pixi's activation, plus bytearmy's variables."""
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PIXI_", "CONDA_"))
        }
        pixi_dir = str(REPO / ".pixi")
        path = [
            entry
            for entry in env.get("PATH", "").split(os.pathsep)
            if entry and not entry.startswith(pixi_dir)
        ]
        env["PATH"] = os.pathsep.join([str(self.root / ".bytearmy" / "bin"), *path])
        env.update(
            TERM="xterm-256color",
            COLORTERM="truecolor",
            TERM_PROGRAM="bytearmy",
            BYTEARMY_URL=self.url,
            BYTEARMY_TOKEN=self.token,
            BYTEARMY_PANE=pane_id,
            BYTEARMY_TASK=f"{PROJECT}/{TASK}",
        )
        return env

    # -- events ------------------------------------------------------------

    def emit(self, event: dict) -> None:
        for queue in self.event_queues:
            queue.put_nowait(event)

    # -- panes -------------------------------------------------------------

    def _new_pane_id(self) -> str:
        self._pane_counter += 1
        return f"p{self._pane_counter}"

    async def open_pane(self, tool: str, arg: str | None = None) -> dict:
        pane_id = self._new_pane_id()
        if tool == "terminal":
            pane = {"id": pane_id, "kind": "terminal", "tool": tool, "title": "Terminal"}
            self._spawn(pane_id, self.terminal_argv)
        elif tool == "claude":
            # arg, if given, is the session name other Claude sessions address this one by.
            instructions = self.agent_instructions(arg) if arg else self.instructions
            command = f"claude --append-system-prompt-file {shlex.quote(str(instructions))}"
            if arg:
                command += f" --name {shlex.quote(arg)}"
            pane = {"id": pane_id, "kind": "terminal", "tool": tool, "title": arg or "Claude"}
            self._spawn(pane_id, [self.shell, "-l", "-i", "-c", command])
        elif tool == "webpage":
            url = arg or PROJECT_GITHUB_URL
            title = urlparse(url).netloc or url
            pane = {"id": pane_id, "kind": "web", "tool": tool, "title": title, "url": url}
        elif tool == "ide":
            url = await self.ensure_vscode()
            pane = {"id": pane_id, "kind": "web", "tool": tool, "title": "IDE", "url": url}
        else:
            raise HTTPException(status_code=400, detail=f"unknown tool {tool!r}")
        self.panes[pane_id] = pane
        self.emit({"type": "pane.opened", "pane": pane})
        return pane

    def _spawn(self, pane_id: str, argv: list[str]) -> None:
        self.ptys[pane_id] = Pty(
            argv,
            cwd=self.task_dir,
            env=self.pane_env(pane_id),
            on_exit=lambda: self.emit({"type": "pane.exited", "id": pane_id}),
        )

    def _require_pane(self, pane_id: str) -> dict:
        if pane_id not in self.panes:
            raise HTTPException(status_code=404, detail=f"no pane {pane_id!r}")
        return self.panes[pane_id]

    async def close_pane(self, id: str) -> dict:
        pane = self._require_pane(id)
        del self.panes[id]
        pty = self.ptys.pop(id, None)
        if pty is not None:
            pty.kill()
        if self.maximized == id:
            self.maximized = None
        self.emit({"type": "pane.closed", "id": id})
        return pane

    async def maximize_pane(self, id: str) -> dict:
        self._require_pane(id)
        self.maximized = id
        self.emit({"type": "pane.maximized", "id": id})
        return {"maximized": id}

    async def restore_panes(self) -> dict:
        self.maximized = None
        self.emit({"type": "pane.maximized", "id": None})
        return {"maximized": None}

    async def list_panes(self) -> list[dict]:
        return list(self.panes.values())

    async def set_status(self, status: str, color: str | None = None) -> dict:
        self.status = {"status": status, "color": color}
        self.emit({"type": "task.status", **self.status})
        return self.status

    def snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "panes": list(self.panes.values()),
            "maximized": self.maximized,
            "status": self.status,
            "task_dir": str(self.task_dir),
        }

    # -- VS Code server ----------------------------------------------------

    async def ensure_vscode(self) -> str:
        """Start one `code serve-web` for the whole app and return the folder URL."""
        async with self._vscode_lock:
            return self.vscode_url or await self._start_vscode()

    async def _start_vscode(self) -> str:
        code = find_vscode()
        if code is None:
            raise HTTPException(status_code=503, detail="VS Code binary not found")
        port = free_port()
        connection_token = secrets.token_urlsafe(16)
        log = (self.root / ".bytearmy" / "serve-web.log").open("ab")
        self.vscode = subprocess.Popen(
            [
                code,
                "serve-web",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--connection-token",
                connection_token,
                "--accept-server-license-terms",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        await _wait_for_port(port, timeout=30)
        url = (
            f"http://127.0.0.1:{port}/?tkn={connection_token}"
            f"&folder={quote(str(self.task_dir))}"
        )
        # serve-web answers 202 while it downloads the VS Code server on first use.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SERVE_WEB_STARTUP_SECONDS
        announced = False
        while await asyncio.to_thread(_http_status, url) == 202:
            if not announced:
                print("Downloading the VS Code server (first run only)...", flush=True)
                announced = True
            if loop.time() > deadline:
                raise HTTPException(status_code=504, detail="VS Code server did not start")
            await asyncio.sleep(1)
        self.vscode_url = url
        return self.vscode_url

    def shutdown(self) -> None:
        for pty in list(self.ptys.values()):
            pty.kill()
        self.ptys.clear()
        if self.vscode is not None and self.vscode.poll() is None:
            try:
                os.killpg(self.vscode.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_status(url: str) -> int:
    """Status of a single GET, without following redirects."""
    try:
        with urllib.request.build_opener(_NoRedirect).open(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


async def _wait_for_port(port: int, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            if asyncio.get_running_loop().time() > deadline:
                raise HTTPException(status_code=504, detail=f"port {port} never opened")
            await asyncio.sleep(0.1)
        else:
            writer.close()
            return


async def _run_until_first_done(*coroutines) -> None:
    """Run the two directions of a socket; stop both when either one ends."""
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


INITIAL_PANES = {
    "a": [("terminal", None)],
    "b": [("terminal", None), ("webpage", "https://github.com"), ("ide", None)],
    "c": [("terminal", None)],
    "d": [("claude", name) for name in AGENT_NAMES],
}

RPC_METHODS = {
    "pane.open": Core.open_pane,
    "pane.close": Core.close_pane,
    "pane.maximize": Core.maximize_pane,
    "pane.restore": Core.restore_panes,
    "pane.list": Core.list_panes,
    "task.set_status": Core.set_status,
}


class RpcCall(BaseModel):
    method: str
    params: dict = {}


def create_app(core: Core, ui_dist: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        core.prepare_root()
        for tool, arg in INITIAL_PANES.get(core.mode, []):
            await core.open_pane(tool, arg)
        yield
        core.shutdown()

    app = FastAPI(lifespan=lifespan)

    def token_ok(supplied: str) -> bool:
        return secrets.compare_digest(supplied.encode(), core.token.encode())

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header.removeprefix("Bearer ").strip()
        if not token_ok(supplied or request.query_params.get("token", "")):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    @app.get("/state", dependencies=[Depends(require_token)])
    async def state() -> dict:
        return core.snapshot()

    @app.post("/rpc", dependencies=[Depends(require_token)])
    async def rpc(call: RpcCall) -> dict:
        handler = RPC_METHODS.get(call.method)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown method {call.method!r}")
        try:
            return {"result": await handler(core, **call.params)}
        except TypeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        if not token_ok(ws.query_params.get("token", "")):
            await ws.close(code=4401)
            return
        queue: asyncio.Queue = asyncio.Queue()
        core.event_queues.add(queue)  # before accept: no event is missed after the handshake
        await ws.accept()

        async def pump_events() -> None:
            while True:
                await ws.send_text(json.dumps(await queue.get()))

        async def wait_for_close() -> None:
            while True:
                await ws.receive_text()

        try:
            await _run_until_first_done(pump_events(), wait_for_close())
        finally:
            core.event_queues.discard(queue)

    @app.websocket("/pty/{pane_id}")
    async def pty_socket(ws: WebSocket, pane_id: str) -> None:
        if not token_ok(ws.query_params.get("token", "")):
            await ws.close(code=4401)
            return
        pty = core.ptys.get(pane_id)
        if pty is None:
            await ws.close(code=4404)
            return
        queue = pty.subscribe()
        await ws.accept()

        async def pump_output() -> None:
            while (chunk := await queue.get()) is not None:
                await ws.send_bytes(chunk)
            await ws.send_text(json.dumps({"type": "exit"}))

        async def pump_input() -> None:
            while True:
                message = json.loads(await ws.receive_text())
                if message["type"] == "input":
                    pty.write(message["data"].encode())
                elif message["type"] == "resize":
                    pty.resize(message["rows"], message["cols"])

        try:
            await _run_until_first_done(pump_output(), pump_input())
        finally:
            pty.unsubscribe(queue)

    if ui_dist is not None and ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))
    return app


async def run(mode: str, root: Path) -> None:
    token = secrets.token_urlsafe(24)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    core = Core(root=root, mode=mode, token=token, url=url)
    app = create_app(core, ui_dist=UI_DIR / "dist")
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3
        )
    )
    serving = asyncio.create_task(server.serve())
    while not server.started and not serving.done():
        await asyncio.sleep(0.05)
    if serving.done():
        await serving
        return

    electron = await asyncio.create_subprocess_exec(
        str(UI_DIR / "node_modules" / ".bin" / "electron"),
        str(UI_DIR),
        env={**os.environ, "BYTEARMY_URL": url, "BYTEARMY_TOKEN": token, "BYTEARMY_SPIKE": mode},
    )
    print(CHECKLISTS[mode].format(uuid="11111111-2222-4333-8444-555555555555"), flush=True)
    # Smoke-test hook: type a line into the first terminal once the shell is up.
    typed = os.environ.get("BYTEARMY_SPIKE_TYPE")
    if typed:
        asyncio.get_running_loop().call_later(4, core.ptys["p1"].write, typed.encode() + b"\n")
    window_closed = asyncio.create_task(electron.wait())
    await asyncio.wait({serving, window_closed}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    if electron.returncode is None:
        electron.terminate()
    await serving
    window_closed.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(INITIAL_PANES))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    asyncio.run(run(args.mode, args.root.resolve()))


if __name__ == "__main__":
    main()
