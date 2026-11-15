"""Cloning a project's repository into a task directory, in the background."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from bytearmy.models import Task
from bytearmy.store import Store

ERROR_CHARACTERS = 500  # how much of git's complaint is kept


class CloneFailed(Exception):
    pass


class AlreadyCloning(Exception):
    """args: the name of the task."""


class Cloner:
    """Runs `git clone` for new tasks; the store learns when a clone starts and how it ends."""

    def __init__(self, store: Store, git: Sequence[str] = ("git",)) -> None:
        self._store = store
        self._git = list(git)
        self._lock = threading.Lock()
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._processes: dict[tuple[str, str], subprocess.Popen] = {}
        self._cancelled: set[tuple[str, str]] = set()

    def valid_branch(self, name: str) -> bool:
        check = subprocess.run(
            [*self._git, "check-ref-format", "--branch", name], capture_output=True, check=False
        )
        return check.returncode == 0

    def start(self, project: str, task: Task, url: str) -> None:
        key = (project, task.slug)
        self._store.clone_started(*key)
        thread = threading.Thread(
            target=self._clone,
            args=(key, url, task.branch, Path(task.path), task.repo),
            daemon=True,
        )
        with self._lock:
            # A second clone into the same place would spoil the first.
            if key in self._threads:
                raise AlreadyCloning(task.name)
            self._threads[key] = thread
        thread.start()

    def cancel(self, project: str, task: str | None = None) -> None:
        """Stop the clones of one task, or of every task of the project, and wait for them."""
        with self._lock:
            keys = [key for key in self._threads if key[0] == project and task in (None, key[1])]
            self._cancelled.update(keys)
            for key in keys:
                self._terminate(self._processes.get(key))
            threads = [self._threads[key] for key in keys]
        for thread in threads:
            thread.join(timeout=10)

    def shutdown(self) -> None:
        with self._lock:
            projects = {project for project, _task in self._threads}
        for project in projects:
            self.cancel(project)

    @staticmethod
    def _terminate(process: subprocess.Popen | None) -> None:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _clone(
        self, key: tuple[str, str], url: str, branch: str, task_dir: Path, repo: str
    ) -> None:
        # Clone beside the final place and rename: a directory named `repo` is always complete.
        temporary = task_dir / f"{repo}.cloning"
        error = ""
        try:
            shutil.rmtree(temporary, ignore_errors=True)
            self._run(key, "clone", "--", url, str(temporary))
            if branch:
                on_remote = self._run(
                    key,
                    *("-C", str(temporary)),
                    *("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"),
                    check=False,
                )
                switch = ["switch", branch] if on_remote == 0 else ["switch", "-c", branch]
                self._run(key, "-C", str(temporary), *switch)
            temporary.rename(task_dir / repo)
        except (CloneFailed, OSError) as failure:
            error = str(failure) or "The clone failed."
            shutil.rmtree(temporary, ignore_errors=True)
        finally:
            with self._lock:
                self._threads.pop(key, None)
                self._cancelled.discard(key)
            self._store.clone_finished(*key, error)

    def _run(self, key: tuple[str, str], *arguments: str, check: bool = True) -> int:
        with self._lock:
            process = subprocess.Popen(
                [*self._git, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # fail instead of asking
                start_new_session=True,
            )
            self._processes[key] = process
        _, complaint = process.communicate()
        with self._lock:
            self._processes.pop(key, None)
            if key in self._cancelled:
                raise CloneFailed("The clone was cancelled.")
        if check and process.returncode != 0:
            raise CloneFailed(complaint.strip()[-ERROR_CHARACTERS:])
        return process.returncode
