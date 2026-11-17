"""The orchestrator: the one agent above all tasks.

It is a Claude session in a terminal of its own, outside the projects, shown in its own tab of
the window. Its `bytearmy` command reaches the projects, the tasks and the inbox instead of a
single task. It runs from the start of bytearmy to its end, and resumes its conversation after
a restart. New messages of the inbox are typed into its terminal, so it can act on them.
"""

from __future__ import annotations

import math
import os
import secrets
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence

from bytearmy.agents import ORCHESTRATOR, write_orchestrator_instructions
from bytearmy.config import ACCOUNTS, ORCHESTRATOR_COMMANDS, OrchestratorSettings, config_of
from bytearmy.models import Message, OrchestratorFile, OrchestratorState, Pane
from bytearmy.ptys import Pty
from bytearmy.store import METADATA_DIR, Store, _write_json
from bytearmy.tools import as_configured, command_line, load_tools
from bytearmy.workspaces import COULD_NOT_START, SPAWNING_TOOL, Workspaces, pane_env

SUBMIT_PAUSE_SECONDS = 0.5  # between a note and its Enter: typed at once, Enter would be pasted
MAX_LISTED = 10  # messages named in one note; the rest are counted
ORCHESTRATOR_SOURCES = ("github",)  # the sources of the messages the orchestrator sees


def describe(messages: Sequence[Message]) -> str:
    """The note for new messages of the inbox: one line, as if the user had typed it."""
    named = "; ".join(
        f"[{message.id}] {message.title}" + (f" ({message.context})" if message.context else "")
        for message in messages[:MAX_LISTED]
    )
    more = f"; and {len(messages) - MAX_LISTED} more" if len(messages) > MAX_LISTED else ""
    count = f"{len(messages)} new message" + ("s" if len(messages) != 1 else "")
    return (
        f"bytearmy: {count} in the inbox: {named}{more}. "
        "Read them with `bytearmy inbox list` and act on them as your instructions say."
    ).replace("\n", " ")


class Orchestrator:
    def __init__(
        self,
        store: Store,
        workspaces: Workspaces,
        shell: Sequence[str] | None = None,
        autostart: bool = False,
        quiet_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Without `autostart`, or with `autostart = false` in the config, the program starts
        only when the window first shows it. `quiet_seconds` defaults to the config's."""
        self._store = store
        self._workspaces = workspaces
        self._shell = list(shell or [os.environ.get("SHELL", "/bin/sh"), "-l", "-i"])
        self._autostart = autostart
        self._quiet = quiet_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._pty: Pty | None = None
        self._key = ""  # the key its program calls the core with; empty while none runs
        self._stopping = False
        self._typed_at = -math.inf  # when the user last typed into its terminal
        self._notes: list[str] = []  # waiting to be typed in
        self._timer: threading.Timer | None = None

    # -- its file ------------------------------------------------------------

    @property
    def _file(self):
        return self._store.root / METADATA_DIR / "orchestrator.json"

    def _load(self) -> OrchestratorFile:
        if not self._file.exists():
            return OrchestratorFile()
        try:
            return OrchestratorFile.model_validate_json(self._file.read_text())
        except ValueError:
            return OrchestratorFile()

    def _save(self, file: OrchestratorFile) -> None:
        _write_json(self._file, file)
        self._store.changed()

    def state(self) -> OrchestratorState:
        with self._lock:
            return OrchestratorState(
                name=ORCHESTRATOR,
                running=self._pty is not None and self._pty.exit_code is None,
                exit_code=self._load().exit_code,
            )

    def _config(self) -> OrchestratorSettings:
        return config_of(self._store.root).orchestrator

    @property
    def _quiet_seconds(self) -> float:
        return self._quiet if self._quiet is not None else self._config().quiet_seconds

    def allows(self, command: str) -> bool:
        """Whether config.toml lets the orchestrator call a `bytearmy` command."""
        return self._config().allows(command)

    def caller_ok(self, key: str) -> bool:
        """Whether a call comes from the orchestrator's program."""
        with self._lock:
            return bool(self._key) and secrets.compare_digest(self._key.encode(), key.encode())

    # -- its program -----------------------------------------------------------

    def start(self) -> None:
        """Start the program when bytearmy starts, unless it ended with an error before."""
        if not (self._autostart and self._config().autostart):
            return
        with self._lock:
            if self._pty is None and self._load().exit_code is None:
                self._start()

    def attach(self, rows: int, cols: int) -> Pty | None:
        """The program, started now if it does not run; None if it ended with an error."""
        with self._lock:
            file = self._load()
            if self._pty is None and file.exit_code is None:
                file.rows, file.cols = rows, cols
                self._save(file)
                self._start()
            return self._pty

    def restart(self) -> None:
        """Start the program again after it ended with an error; it resumes its session."""
        with self._lock:
            old, self._pty = self._pty, None
            file = self._load()
            file.exit_code = None
            self._save(file)
        if old is not None:
            old.kill()
        with self._lock:
            if self._pty is None:
                self._start()

    def write(self, data: bytes) -> None:
        """What the user types into the terminal."""
        with self._lock:
            self._typed_at = self._clock()
            pty = self._pty
        if pty is not None:
            pty.write(data)

    def resized(self, rows: int, cols: int) -> None:
        with self._lock:
            if self._pty is not None:
                self._pty.resize(rows, cols)
            file = self._load()
            if (file.rows, file.cols) != (rows, cols):
                file.rows, file.cols = rows, cols
                _write_json(self._file, file)  # no news for the window

    def _start(self) -> None:
        """Start the program; the caller holds the lock."""
        file = self._load()
        restoring = bool(file.session)
        if not file.session:
            file.session = str(uuid.uuid4())
        config = config_of(self._store.root)
        settings = config.orchestrator
        tool = load_tools(self._store.root, settings=config.tools).get(SPAWNING_TOOL)
        if tool is None or not tool.agent:
            file.exit_code = COULD_NOT_START
            self._save(file)
            return
        self._save(file)
        pane = Pane(
            id=ORCHESTRATOR,
            tool=tool.id,
            title="Orchestrator",
            session=file.session,
            agent=ORCHESTRATOR,
        )
        instructions = write_orchestrator_instructions(
            self._store.root,
            self._store.root / METADATA_DIR / "agents",
            settings.denied(ORCHESTRATOR_COMMANDS),
        )
        line = command_line(
            as_configured(tool, settings), pane, restoring, instructions, settings.flags()
        )
        argv = [*self._shell, "-c", line] if line else self._shell
        self._key = secrets.token_urlsafe(24)
        env = pane_env(os.environ, sys.prefix)
        env["PATH"] = os.pathsep.join(filter(None, [str(self._workspaces.bin_dir), env["PATH"]]))
        env.update(
            BYTEARMY_URL=self._workspaces.url,
            BYTEARMY_TOKEN=self._key,
            BYTEARMY_ROLE=ORCHESTRATOR,
        )
        env.update(ACCOUNTS.env(config.github))
        env.pop("BYTEARMY_TASK", None)
        env.pop("BYTEARMY_PANE", None)
        started: list[Pty] = []
        self._pty = Pty(
            argv,
            self._store.root,
            env,
            file.rows,
            file.cols,
            on_exit=lambda code: self._ended(started[0] if started else None, code),
        )
        started.append(self._pty)
        self._store.changed()

    def _ended(self, pty: Pty | None, code: int) -> None:
        """The program ended by itself. Ended well, it starts again when next shown; ended
        with an error, its output stays until the user starts it again."""
        with self._lock:
            if pty is not None and pty is not self._pty:
                return  # an earlier program, replaced meanwhile
            self._key = ""
            if self._stopping:
                return
            if code == 0:
                self._pty = None
                self._store.changed()
            else:
                file = self._load()
                file.exit_code = code
                self._save(file)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            pty, self._pty = self._pty, None
            if self._timer is not None:
                self._timer.cancel()
        if pty is not None:
            pty.kill()

    # -- notes from bytearmy ------------------------------------------------------

    def announce(self, messages: Sequence[Message]) -> None:
        """Tell the orchestrator about new messages of the inbox; it sees only GitHub's."""
        messages = [message for message in messages if message.source in ORCHESTRATOR_SOURCES]
        if messages and self._config().announce_inbox:
            self.notify(describe(messages))

    def notify(self, text: str) -> None:
        """Type a line into the terminal once the user has not typed for a while.

        Claude Code keeps what is typed while it works, so the note needs no pause in its work.
        """
        with self._lock:
            self._notes.append(text.replace("\n", " "))
        self._deliver()

    def _retry(self) -> None:
        with self._lock:
            self._timer = None
        self._deliver()

    def _deliver(self) -> None:
        with self._lock:
            if self._stopping or not self._notes:
                return
            pty = self._pty
            waited = self._clock() - self._typed_at
            quiet = self._quiet_seconds
            if pty is None or pty.exit_code is not None or waited < quiet:
                # Try again later: once the user paused, or once the program runs.
                delay = max(quiet - waited, 1.0) if pty is not None else max(quiet, 1.0)
                if self._timer is None:
                    self._timer = threading.Timer(delay, self._retry)
                    self._timer.daemon = True
                    self._timer.start()
                return
            notes, self._notes = self._notes, []
        pty.write(" ".join(notes).encode())
        time.sleep(SUBMIT_PAUSE_SECONDS)
        pty.write(b"\r")
