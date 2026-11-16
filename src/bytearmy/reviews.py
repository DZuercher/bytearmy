"""The review of a task's changes: diffle (https://diffle.app) shows them in a diff pane and
keeps the user's comments; when the pane closes, the comments become a message to an agent."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bytearmy.models import DiffMode
from bytearmy.services import STOP_GRACE_SECONDS, free_port
from bytearmy.tools import ToolFailed

PROGRAM = "diffle"
DEFAULT_BRANCHES = ("origin/main", "origin/master", "main", "master")
REVIEW_HEADING = "Review comments from the user"
STARTUP_SECONDS = 60
POLL_SECONDS = 0.1
REQUEST_SECONDS = 10
LOG_TAIL = 500

Key = tuple[str, str, str]  # project, task, pane


class ReviewFailed(RuntimeError):
    """diffle did not answer as it should; the message says why."""


def _git(repo: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", *arguments],
        check=False,
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def default_branch(repo: Path) -> str | None:
    """The branch the task's work goes into: origin's HEAD, else a usual name."""
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    for ref in filter(None, [head, *DEFAULT_BRANCHES]):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return None


def comparison(repo: Path, mode: DiffMode, base: str = "") -> tuple[str, str]:
    """What diffle compares, as its revision argument, and a label for the base.

    branch: everything since HEAD left the task's base branch (the default branch without
    one), uncommitted changes included.
    uncommitted: what changed since the last commit. Without a default branch, or without
    a commit to fork from, branch is the same as uncommitted.
    """
    if mode == "branch" and _git(repo, "rev-parse", "--verify", "--quiet", "HEAD"):
        based = f"origin/{base}"
        on_base = base and _git(repo, "rev-parse", "--verify", "--quiet", f"{based}^{{commit}}")
        branch = based if on_base else default_branch(repo)
        if branch is not None and _git(repo, "merge-base", "HEAD", branch):
            return branch, branch
    return "working", "HEAD"


def format_review(label: str, threads: str) -> str:
    """The review as a message to an agent; `threads` is diffle's prompt of the open threads."""
    body = threads.strip() or "The user finished the review without comments."
    return f"{REVIEW_HEADING} on the changes against {label}:\n\n{body}\n"


@dataclass
class _Server:
    process: subprocess.Popen
    url: str
    label: str
    log: Path


class Reviews:
    """The diffle servers of the diff panes: one per pane, started when the pane is shown."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._lock = threading.Lock()  # guards the fields below
        self._starts: dict[Key, threading.Lock] = {}
        self._servers: dict[Key, _Server] = {}
        self._stopping = False

    def url(
        self, key: Key, repo: Path, mode: DiffMode, env: Mapping[str, str], base: str = ""
    ) -> str:
        """Where the pane's diffle answers; starts it if it does not run."""
        return self._server(key, repo, mode, env, base).url

    def finish(
        self, key: Key, repo: Path, mode: DiffMode, env: Mapping[str, str], base: str = ""
    ) -> tuple[str, int]:
        """The review written in the pane, as a message to an agent, and how many threads
        it has.

        Its open threads are resolved, so the next review starts without them.
        """
        server = self._server(key, repo, mode, env, base)
        try:
            threads = json.loads(self._call(server, "GET", "/api/threads?state=open"))["threads"]
        except (ValueError, KeyError, TypeError) as error:
            raise ReviewFailed(f"diffle listed its threads in an unknown form: {error}") from error
        text = self._call(server, "GET", "/api/threads/export") if threads else ""
        for thread in threads:
            self._call(server, "PUT", f"/api/threads/{thread['id']}/resolved", {"resolved": True})
        return format_review(server.label, text), len(threads)

    def stop(self, keys: list[Key]) -> None:
        """End the servers of these panes; their open comments stay with diffle."""
        with self._lock:
            ended = [server for key in keys if (server := self._servers.pop(key, None))]
        for server in ended:
            _stop(server.process)

    def running(self) -> list[Key]:
        """The panes whose diffle runs."""
        with self._lock:
            return list(self._servers)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
        self.stop(self.running())

    def _server(
        self, key: Key, repo: Path, mode: DiffMode, env: Mapping[str, str], base: str
    ) -> _Server:
        with self._lock:
            start = self._starts.setdefault(key, threading.Lock())
        with start:  # a pane that asks twice at once gets one server
            with self._lock:
                server = self._servers.get(key)
            if server is None or server.process.poll() is not None:
                server = self._start(key, repo, mode, env, base)
            return server

    def _start(
        self, key: Key, repo: Path, mode: DiffMode, env: Mapping[str, str], base: str
    ) -> _Server:
        # The pane's environment leaves out bytearmy's own, where pixi installs diffle.
        program = shutil.which(PROGRAM) or shutil.which(PROGRAM, path=env.get("PATH"))
        if program is None:
            raise ToolFailed(
                "diffle is not installed: the diff pane needs it. See https://diffle.app."
            )
        revision, label = comparison(repo, mode, base)
        port = free_port()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log = self._log_dir / f"diffle-{'-'.join(key)}.log"
        command = [program, revision, "-C", str(repo), "-p", str(port), "--no-open"]
        with self._lock:
            if self._stopping:
                raise ToolFailed("diffle was not started: bytearmy is shutting down.")
            with log.open("wb") as output:
                process = subprocess.Popen(
                    command,
                    cwd=repo,
                    env=dict(env),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,  # on its way out, it prints the open comments
                    stderr=output,
                    start_new_session=True,
                )
            server = _Server(process, f"http://127.0.0.1:{port}/", label, log)
            self._servers[key] = server  # from here on `stop` ends it

        deadline = time.monotonic() + STARTUP_SECONDS
        while not _answers(server.url + "api/server"):
            if process.poll() is not None:
                with self._lock:
                    self._servers.pop(key, None)
                raise ToolFailed(
                    f"diffle could not start (exit code {process.returncode}). " + _tail(log)
                )
            if time.monotonic() > deadline:
                self.stop([key])
                raise ToolFailed(f"diffle did not answer within {STARTUP_SECONDS} seconds.")
            time.sleep(POLL_SECONDS)
        return server

    def _call(self, server: _Server, method: str, path: str, body: object = None) -> str:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(server.url + path.lstrip("/"), data=data, method=method)
        try:
            with _opener().open(request, timeout=REQUEST_SECONDS) as response:
                return response.read().decode()
        except (OSError, ValueError) as error:
            raise ReviewFailed(f"diffle did not answer {method} {path}: {error}") from error


def _opener() -> urllib.request.OpenerDirector:
    # No proxy: diffle runs on this machine, whatever the environment says.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _answers(url: str) -> bool:
    try:
        with _opener().open(url, timeout=REQUEST_SECONDS):
            return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def _stop(process: subprocess.Popen) -> None:
    """End diffle; by force if it does not listen."""
    process.terminate()
    try:
        process.wait(timeout=STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _tail(log: Path) -> str:
    try:
        return log.read_text(errors="replace").strip()[-LOG_TAIL:]
    except OSError:
        return ""
