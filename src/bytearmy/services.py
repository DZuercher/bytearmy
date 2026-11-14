"""Programs without a terminal: the services web tools need, and external tools."""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bytearmy.models import Tool
from bytearmy.tools import ToolFailed, fill

STARTUP_SECONDS = 300  # a first start may have to download the program
POLL_SECONDS = 0.2
STOP_GRACE_SECONDS = 3
EXTERNAL_GRACE_SECONDS = 2  # a program that still runs after this has its window
LOG_TAIL = 500
STILL_PREPARING = 202  # what VS Code's server answers while it downloads itself


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class _Running:
    process: subprocess.Popen
    url: str


class Services:
    """The services of the web tools: at most one per tool, started when first needed."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._lock = threading.Lock()  # guards the fields below
        self._starts: dict[str, threading.Lock] = {}
        self._running: dict[str, _Running] = {}
        self._stopping = False

    def url(self, tool: Tool, env: Mapping[str, str]) -> str:
        """Where the tool's service answers.

        Starts the service if it does not run, and returns once it answers.
        """
        with self._lock:
            start = self._starts.setdefault(tool.id, threading.Lock())
        with start:  # panes that ask at the same time share one start
            with self._lock:
                running = self._running.get(tool.id)
            if running is None or running.process.poll() is not None:
                running = self._start(tool, env)
            return running.url

    def _start(self, tool: Tool, env: Mapping[str, str]) -> _Running:
        values = {"port": str(free_port()), "token": secrets.token_urlsafe(16)}
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self._log_dir / f"{tool.id}.log"
        with self._lock:
            if self._stopping:
                raise ToolFailed(f"{tool.name} was not started: bytearmy is shutting down.")
            process = _spawn(fill(tool.service.command, values), None, env, log_file)
            running = _Running(process, fill(tool.service.url, values))
            self._running[tool.id] = running  # from here on `shutdown` ends it

        deadline = time.monotonic() + STARTUP_SECONDS
        while not _answers(running.url):
            if process.poll() is not None:
                raise ToolFailed(
                    f"{tool.name} could not start (exit code {process.returncode}). "
                    + _tail(log_file)
                )
            if time.monotonic() > deadline:
                _stop(process)
                raise ToolFailed(f"{tool.name} did not answer within {STARTUP_SECONDS} seconds.")
            time.sleep(POLL_SECONDS)
        return running

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            processes = [running.process for running in self._running.values()]
        for process in processes:
            _stop(process)


def launch(tool: Tool, command: str, directory: Path, env: Mapping[str, str], log: Path) -> None:
    """Start a program that opens a window of its own; report it if it fails at once."""
    log.parent.mkdir(parents=True, exist_ok=True)
    process = _spawn(command, directory, env, log)
    try:
        code = process.wait(timeout=EXTERNAL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        raise ToolFailed(f"{tool.name} failed (exit code {code}). " + _tail(log))


def _spawn(
    command: str, directory: Path | None, env: Mapping[str, str], log_file: Path
) -> subprocess.Popen:
    """Run a shell command detached from the core, with its output in a file."""
    with log_file.open("wb") as log:
        return subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=directory,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _answers(url: str) -> bool:
    """Whether a GET of the URL gets an answer other than "still preparing"."""
    # No proxy: the service is on this machine, whatever the environment says.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(url, timeout=10) as response:
            return response.status != STILL_PREPARING
    except urllib.error.HTTPError:  # a redirect or an error page is an answer too
        return True
    except OSError:  # nothing listens yet
        return False


def _stop(process: subprocess.Popen) -> None:
    """End a program and everything it started; by force if it does not listen."""
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:  # a reaped pid may belong to someone else by now
            return
        try:
            os.killpg(process.pid, signal_number)
        except (ProcessLookupError, PermissionError):
            return  # it ended a moment ago; macOS answers so until it has been reaped
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _tail(log_file: Path) -> str:
    try:
        return log_file.read_text(errors="replace").strip()[-LOG_TAIL:]
    except OSError:
        return ""
