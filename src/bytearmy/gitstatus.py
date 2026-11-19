"""The git status of the task clones of projects with a GitHub URL, read in the background.

Every few seconds each clone tells whether it has uncommitted changes and how far its branch
is ahead of and behind origin's. Origin's branch is fetched now and then, so that the
commits pushed elsewhere show up. A branch origin does not have is ahead by the commits no
remote branch holds.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from bytearmy.config import project_github_env
from bytearmy.models import GitStatus
from bytearmy.store import Store

log = logging.getLogger(__name__)

STATUS_SECONDS = 5.0  # how often the clones are asked for their status
FETCH_SECONDS = 60.0  # how often origin's branch is fetched into a clone
TIMEOUT_SECONDS = 20.0

Key = tuple[str, str]


class GitStatuses:
    """Remembers the git status of each task clone; a thread keeps it up to date."""

    def __init__(
        self,
        store: Store,
        git: Sequence[str] = ("git",),
        enabled: bool = True,
        status_seconds: float = STATUS_SECONDS,
        fetch_seconds: float = FETCH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Without `enabled`, `start` starts no thread: nothing is known but what `check` finds."""
        self._store = store
        self._git = list(git)
        self._enabled = enabled
        self._status_seconds = status_seconds
        self._fetch_seconds = fetch_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._statuses: dict[Key, GitStatus] = {}
        self._fetched: dict[Key, float] = {}  # when origin's branch was last fetched
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self, project: str, task: str) -> GitStatus | None:
        """The task clone's status; None if it has none, or none is known yet."""
        with self._lock:
            return self._statuses.get((project, task))

    # -- the thread ----------------------------------------------------------

    def start(self) -> None:
        if not self._enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._poll, name="git-status", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # the thread must live on; the next turn tries again
                log.exception("Reading the git status of the clones failed")
            self._stop.wait(self._status_seconds)

    def check(self) -> None:
        """Ask every clone once; a change of what is known counts as a change of the store."""
        statuses: dict[Key, GitStatus] = {}
        for project in self._store.app_state().projects:
            if not project.github_url:
                continue
            env = {  # fail instead of asking; as the GitHub account of the project
                **os.environ,
                **project_github_env(self._store.root, Path(project.path)),
                "GIT_TERMINAL_PROMPT": "0",
            }
            for task in project.tasks:
                if task.clone != "ready" or self._stop.is_set():
                    continue
                key = (project.slug, task.slug)
                clone = Path(task.path) / task.repo
                try:
                    statuses[key] = self._read(key, clone, env)
                except (OSError, subprocess.SubprocessError):
                    pass  # e.g. a clone that is being deleted: it shows no status
        with self._lock:
            changed = statuses != self._statuses
            self._statuses = statuses
            self._fetched = {key: self._fetched[key] for key in statuses if key in self._fetched}
        if changed:
            self._store.changed()

    def _read(self, key: Key, clone: Path, env: dict[str, str]) -> GitStatus:
        def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*self._git, "-C", str(clone), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=check,
                env=env,
            )

        # Without the index lock, so that a commit the user makes meanwhile does not fail.
        dirty = bool(git("--no-optional-locks", "status", "--porcelain").stdout.strip())
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
        upstream = f"refs/remotes/origin/{branch}"
        if branch and self._clock() - self._fetched.get(key, -math.inf) >= self._fetch_seconds:
            self._fetched[key] = self._clock()
            # Fails if origin has no such branch; then the one known stays.
            git(
                "fetch",
                "--quiet",
                "--no-tags",
                "origin",
                f"+refs/heads/{branch}:{upstream}",
                check=False,
            )
        if (
            branch
            and git("rev-parse", "--verify", "--quiet", upstream, check=False).returncode == 0
        ):
            counts = git("rev-list", "--left-right", "--count", f"{upstream}...HEAD").stdout.split()
            behind, ahead = int(counts[0]), int(counts[1])
        else:
            ahead = int(git("rev-list", "--count", "HEAD", "--not", "--remotes").stdout)
            behind = 0
        return GitStatus(dirty=dirty, ahead=ahead, behind=behind)
