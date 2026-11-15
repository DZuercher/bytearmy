"""The pull request of a task's branch, found with `git ls-remote` in the background.

GitHub keeps the head of every pull request at `refs/pull/<number>/head`. The pull request
of a branch is the one whose head is the commit the branch points at; of several, the
newest. Git asks the remote with the credentials it clones with, so private repositories
work and nothing beyond git is needed.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence

from bytearmy.models import Project, Task
from bytearmy.store import Store

MAX_AGE_SECONDS = 60.0  # how long a lookup is trusted before the remote is asked again
TIMEOUT_SECONDS = 20.0
PULL_HEAD = re.compile(r"refs/pull/(\d+)/head")

Key = tuple[str, str]


def pull_request_number(listing: str, branch: str) -> int | None:
    """The newest pull request whose head is the branch's commit, in `git ls-remote` output."""
    head = ""
    pulls: list[tuple[int, str]] = []
    for line in listing.splitlines():
        commit, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}":
            head = commit
        elif match := PULL_HEAD.fullmatch(ref):
            pulls.append((int(match[1]), commit))
    return max((number for number, commit in pulls if head and commit == head), default=None)


class PullRequests:
    """Remembers the pull request page of each task; a stale one is looked up again."""

    def __init__(
        self,
        store: Store,
        git: Sequence[str] = ("git",),
        max_age: float = MAX_AGE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._git = list(git)
        self._max_age = max_age
        self._clock = clock
        self._lock = threading.Lock()
        self._pages: dict[Key, tuple[float, str]] = {}  # (when it was looked up, page)
        self._looking: set[Key] = set()

    def page(self, project: Project, task: Task, wait: bool = False) -> str:
        """The page of the task's pull request; empty if it has none, or none is known yet.

        Without `wait`, a stale page is looked up in the background, and the store counts
        a change once it is found. With `wait`, it is looked up before the answer.
        """
        if not (task.branch and project.github_url and project.github_page):
            return ""
        key = (project.slug, task.slug)
        with self._lock:
            checked, page = self._pages.get(key, (None, ""))
            fresh = checked is not None and self._clock() - checked < self._max_age
            if fresh or (key in self._looking and not wait):
                return page
            self._looking.add(key)
        arguments = (key, project.github_url, project.github_page, task.branch)
        if wait:
            return self._look_up(*arguments)
        threading.Thread(target=self._look_up, args=arguments, daemon=True).start()
        return page

    def _look_up(self, key: Key, url: str, github_page: str, branch: str) -> str:
        try:
            listing = subprocess.run(
                [*self._git, "ls-remote", "--", url, f"refs/heads/{branch}", "refs/pull/*/head"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # fail instead of asking
            ).stdout
            number = pull_request_number(listing, branch)
            page = f"{github_page}/pull/{number}" if number else ""
        except (OSError, subprocess.SubprocessError):
            with self._lock:
                self._looking.discard(key)
                page = self._pages.get(key, (0.0, ""))[1]
                self._pages[key] = (self._clock(), page)  # not again before it is stale
            return page
        with self._lock:
            self._looking.discard(key)
            changed = self._pages.get(key, (0.0, ""))[1] != page
            self._pages[key] = (self._clock(), page)
        if changed:
            self._store.changed()
        return page
