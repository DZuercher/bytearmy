"""Programs running in pseudo-terminals, readable by any number of listeners."""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import struct
import subprocess
import sys
import termios
import threading
from collections.abc import Callable
from pathlib import Path

REPLAY_BYTES = 1_000_000  # how much output a late listener still gets
KILL_GRACE_SECONDS = 3

# Runs in a fresh interpreter between fork and exec: makes the pseudo-terminal the
# controlling terminal of the new session, then becomes the program.
_BECOME = (
    "import fcntl, os, sys, termios\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def _set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Pty:
    """One program attached to a pseudo-terminal.

    `on_exit` is called with the exit code once the program has ended, before the
    listeners learn about it.
    """

    def __init__(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        rows: int,
        cols: int,
        on_exit: Callable[[int], None],
    ) -> None:
        self._master, slave = os.openpty()
        _set_size(self._master, rows, cols)
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-c", _BECOME, *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        finally:
            os.close(slave)
        self._on_exit = on_exit
        self._terminal = threading.Lock()  # guards the terminal's file descriptor
        self._lock = threading.Lock()  # guards the replay buffer and the listeners
        self._replay = bytearray()
        self._listeners: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self.exit_code: int | None = None
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self._master, 65536)
            except OSError:  # Linux reports the end of the program as an error
                data = b""
            if not data:
                break
            with self._lock:
                self._replay += data
                del self._replay[: max(0, len(self._replay) - REPLAY_BYTES)]
                self._tell(data)
        code = self._process.wait()
        with self._terminal:
            os.close(self._master)
            self._master = -1
        self._on_exit(code)
        with self._lock:
            self.exit_code = code
            self._tell(None)

    def _tell(self, message: bytes | None) -> None:
        """Hand a chunk, or the end, to every listener. The caller holds the lock."""
        for queue, loop in list(self._listeners.items()):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
            except RuntimeError:  # the listener's event loop is gone
                del self._listeners[queue]

    def listen(self) -> asyncio.Queue:
        """A queue of output chunks for the running event loop; None marks the end.

        The queue starts with what was printed so far, so no output is lost or doubled.
        """
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            if self._replay:
                queue.put_nowait(bytes(self._replay))
            if self.exit_code is not None:
                queue.put_nowait(None)
            else:
                self._listeners[queue] = asyncio.get_running_loop()
        return queue

    def stop_listening(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.pop(queue, None)

    def output(self) -> bytes:
        """What the program printed so far; the last REPLAY_BYTES of it."""
        with self._lock:
            return bytes(self._replay)

    def write(self, data: bytes) -> None:
        with self._terminal:
            try:
                while data:
                    data = data[os.write(self._master, data) :]
            except OSError:
                pass  # the program has ended, or is about to

    def resize(self, rows: int, cols: int) -> None:
        with self._terminal:
            if self._master >= 0:
                _set_size(self._master, rows, cols)

    def hang_up(self) -> None:
        """Tell the program to end, like a closing terminal window does."""
        self._signal(signal.SIGHUP)

    def kill(self) -> None:
        """End the program and wait until it is gone; by force if it does not listen."""
        self.hang_up()
        self._reader.join(timeout=KILL_GRACE_SECONDS)
        if self._reader.is_alive():
            self._signal(signal.SIGKILL)
            self._reader.join(timeout=KILL_GRACE_SECONDS)

    def _signal(self, signal_number: int) -> None:
        if self._process.returncode is None:  # a reaped pid may belong to someone else by now
            try:
                os.killpg(self._process.pid, signal_number)
            except (ProcessLookupError, PermissionError):
                pass  # it ended a moment ago; macOS answers so until it has been reaped
