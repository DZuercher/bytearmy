"""Helpers shared by the checks: reading terminals, a repository to clone from, a git on hold,
a stand-in for the service of a web tool, an end to the programs a failing check left running,
the `bytearmy` command run as a pane."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from bytearmy import cli, workspaces

# The fixture repositories must not depend on the git configuration of the machine.
ISOLATED = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

FAILED = pytest.StashKey[bool]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed:
        item.stash[FAILED] = True
    return report


@pytest.fixture(autouse=True)
def claude_config(tmp_path_factory, monkeypatch) -> Path:
    """Claude's configuration for the check, never the user's: agent panes trust directories."""
    directory = tmp_path_factory.mktemp("claude-config")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(directory))
    return directory / ".claude.json"


@pytest.fixture(autouse=True)
def agents_ready_at_once(monkeypatch):
    """The stand-in agents of the checks take typing at once, not only after the seconds a
    Claude session needs to start; the checks of that wait set it themselves."""
    monkeypatch.setattr(workspaces, "READY_AFTER_SECONDS", 0)
    monkeypatch.setattr(workspaces, "QUIET_SECONDS", 0)
    monkeypatch.setattr(workspaces, "START_CHECK_SECONDS", 0.3)  # a listener's start check


@pytest.fixture(autouse=True)
def end_leftover_programs(request, monkeypatch):
    """When a check fails, end the programs it started that still run, and their children.

    A failing check skips the part that would have stopped them. A program that moved into
    a process group of its own, like a job of an interactive shell, is not found.
    """
    started: list[int] = []
    create = subprocess.Popen.__init__

    def recording(self, *args, **kwargs):
        create(self, *args, **kwargs)
        started.append(self.pid)

    monkeypatch.setattr(subprocess.Popen, "__init__", recording)
    yield
    if request.node.stash.get(FAILED, False):
        leftover = leftover_programs(set(started))
        for pid in leftover:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if leftover:
            print("ended what the check left running:", *leftover.values(), sep="\n  ")


def leftover_programs(started: set[int]) -> dict[int, str]:
    """The live processes started by the check, or in a process group one of them leads."""
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,pgid=,stat=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    found = {}
    for line in listing.stdout.splitlines():
        pid, group, stat, *command = line.split(None, 3)
        if stat.startswith("Z") or int(pid) == os.getpid():
            continue  # ended and not yet collected, or this process
        if int(pid) in started or int(group) in started:
            found[int(pid)] = " ".join(command)
    return found


def git(*arguments: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", *arguments],
        cwd=cwd,
        env=ISOLATED,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


async def read_until(output: asyncio.Queue, pattern: bytes, timeout: float = 5) -> bytes:
    """Collect terminal output until it matches, or fail showing what arrived."""
    seen = b""
    try:
        async with asyncio.timeout(timeout):
            while not re.search(pattern, seen):
                chunk = await output.get()
                assert chunk is not None, f"ended before {pattern!r}; saw {seen!r}"
                seen += chunk
    except TimeoutError:
        pytest.fail(f"no match for {pattern!r} within {timeout}s; saw {seen!r}")
    return seen


async def read_to_end(output: asyncio.Queue, timeout: float = 5) -> bytes:
    seen = b""
    async with asyncio.timeout(timeout):
        while (chunk := await output.get()) is not None:
            seen += chunk
    return seen


def wait_until(condition: Callable[[], object], what: str, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen within {timeout}s"
        time.sleep(0.01)


def running(pid: int) -> bool:
    """Whether the process is alive; one that has ended but was not collected yet is not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/stat")  # Linux; elsewhere an ended process is collected at once
    return not (status.exists() and status.read_text().rsplit(")", 1)[1].split()[0] == "Z")


FAKE_SERVICE = '''\
"""Stands in for the service of a web tool: fake.py <port> <token> <preparing> <pids>."""
import http.server
import os
import signal
import subprocess
import sys

port, token, pids = int(sys.argv[1]), sys.argv[2], sys.argv[4]
child = subprocess.Popen(["sleep", "60"])  # a service may start programs of its own
with open(pids, "a") as file:
    file.write(f"{os.getpid()} {child.pid}\\n")


def note(kind, text):
    with open(f"{pids}.{kind}", "a") as file:
        file.write(text + "\\n")


def leave(*_):
    note("farewell", "asked to end")
    os._exit(0)


if signal.getsignal(signal.SIGTERM) is not signal.SIG_IGN:  # unless told to ignore it
    signal.signal(signal.SIGTERM, leave)


class Handler(http.server.BaseHTTPRequestHandler):
    preparing = int(sys.argv[3])  # so many requests are answered with "still preparing"

    def do_GET(self):
        note("requests", self.path.split("?")[0])
        if Handler.preparing > 0:
            Handler.preparing -= 1
            self.send_response(202)
        elif self.path.startswith(f"/?tkn={token}"):
            self.send_response(302)
            self.send_header("Location", "/ready")
        else:
            self.send_response(403)
        self.end_headers()

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@dataclass
class FakeService:
    """What a tool file needs to run the stand-in service, and what became of its starts."""

    script: Path
    pids: Path
    url: str = "http://127.0.0.1:{port}/?tkn={token}"

    def command(self, preparing: int = 0, before: str = "") -> str:
        program = f"{sys.executable} {self.script} {{port}} {{token}} {preparing} {self.pids}"
        return f"{before}exec {program}"

    def started(self) -> list[tuple[int, int]]:
        """Per start: the process id of the service and of the program it started."""
        if not self.pids.exists():
            return []
        return [tuple(map(int, line.split())) for line in self.pids.read_text().splitlines()]

    def noted(self, kind: str) -> list[str]:
        """What the service wrote down: the paths of its "requests", or its "farewell"."""
        notes = Path(f"{self.pids}.{kind}")
        return notes.read_text().splitlines() if notes.exists() else []


@pytest.fixture
def fake_service(tmp_path) -> FakeService:
    script = tmp_path / "fake_service.py"
    script.write_text(FAKE_SERVICE)
    return FakeService(script=script, pids=tmp_path / "fake-service.pids")


@pytest.fixture
def remote(tmp_path) -> Path:
    """A repository to clone from: `main` with one commit, `feature` with one more."""
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("hello\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "first", cwd=work)
    git("switch", "-c", "feature", cwd=work)
    (work / "feature.txt").write_text("only on feature\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "second", cwd=work)
    git("switch", "main", cwd=work)
    bare = tmp_path / "widgets.git"
    git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    return bare


@dataclass
class GatedGit:
    """A stand-in for git that waits until `release()` before it runs the real git."""

    command: list[str]
    gate: Path
    pid_file: Path

    def release(self) -> None:
        self.gate.touch()

    def running(self) -> bool:
        """Whether a held-back git process is alive."""
        if not self.pid_file.exists():
            return False
        try:
            os.kill(int(self.pid_file.read_text()), 0)
        except (ProcessLookupError, ValueError):
            return False
        return True

    def wait_until_running(self, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not self.running():
            assert time.monotonic() < deadline, "the held-back git never started"
            time.sleep(0.01)

    def wait_until_gone(self, timeout: float = 5) -> bool:
        deadline = time.monotonic() + timeout
        while self.running():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True


@pytest.fixture
def gated_git(tmp_path):
    gate = tmp_path / "gate"
    pid_file = tmp_path / "gated-git.pid"
    script = tmp_path / "gated-git"
    script.write_text(
        "#!/bin/sh\n"
        # `check-ref-format` only validates a name; holding it back would hold back the request.
        'if [ "$1" != check-ref-format ]; then\n'
        f'  echo $$ > "{pid_file}"\n'
        f'  while [ ! -e "{gate}" ]; do sleep 0.02; done\n'
        "fi\n"
        'exec git "$@"\n'
    )
    script.chmod(0o755)
    held = GatedGit(command=[str(script)], gate=gate, pid_file=pid_file)
    yield held
    held.release()  # never leave a waiting process behind


@pytest.fixture
def bytearmy(monkeypatch, capsys):
    """Run the command as the pane whose variables are given: (exit code, what it printed)."""

    def run(env: dict[str, str], *args: str) -> tuple[int, str]:
        for name in ("BYTEARMY_URL", "BYTEARMY_TOKEN", "BYTEARMY_PANE"):
            monkeypatch.setenv(name, env[name])
        code = cli.main(list(args))
        out, err = capsys.readouterr()
        return code, (out + err).strip()

    return run
