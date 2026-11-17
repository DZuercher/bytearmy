"""Cloning a project's repository into a task directory, in the background.

The repository is downloaded once per project, into a bare mirror in the project's metadata
directory. A task clones from the mirror after fetching into it: git hard-links the objects of
a local clone, so a task costs no download and hardly any disk, yet is a repository of its own.

The mirror holds no Git LFS files: the task checks out their pointers, and once its origin is
the project's URL, git-lfs downloads the files from there.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from bytearmy.config import project_github_env
from bytearmy.models import Task
from bytearmy.store import METADATA_DIR, ProjectNotFound, Store

ERROR_CHARACTERS = 500  # how much of git's complaint is kept
MIRROR = "mirror.git"
FETCH_SECONDS = 15  # how long a list of the branches waits for origin


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
        self._mirror_locks: dict[str, threading.Lock] = {}  # per project: one writer at a time

    def valid_branch(self, name: str) -> bool:
        check = subprocess.run(
            [*self._git, "check-ref-format", "--branch", name], capture_output=True, check=False
        )
        return check.returncode == 0

    def branches(self, project: str) -> list[str]:
        """The branches of the project's repository, the default one first, then the most
        recently committed to. The mirror fetches first, unless a clone holds it; without a
        mirror there are none."""
        mirror = Path(self._store.project(project).path) / METADATA_DIR / MIRROR
        if not mirror.exists():
            return []
        env = {**os.environ, **self._github_env(project), "GIT_TERMINAL_PROMPT": "0"}

        def git(*arguments: str, timeout: float = 30) -> str:
            try:
                done = subprocess.run(
                    [*self._git, "-C", str(mirror), *arguments],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return ""
            return done.stdout if done.returncode == 0 else ""

        with self._lock:
            lock = self._mirror_locks.setdefault(project, threading.Lock())
        if lock.acquire(blocking=False):
            try:
                git("fetch", "--quiet", "--prune", "origin", timeout=FETCH_SECONDS)
            finally:
                lock.release()
        names = git(
            "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads"
        ).split()
        default = git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
        return sorted(names, key=lambda name: name != default)  # stable: dates stay in order

    def switch(self, project: str, task: Task, branch: str) -> None:
        """Put the task's finished clone on `branch`: origin's if there is one, else a new one
        from where the clone stands. Uncommitted changes come along."""
        repo = Path(task.path) / task.repo
        env = {**os.environ, **self._github_env(project), "GIT_TERMINAL_PROMPT": "0"}

        def git(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*self._git, "-C", str(repo), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )

        def exists(ref: str) -> bool:
            return git("rev-parse", "--verify", "--quiet", ref).returncode == 0

        # The clone knows origin's branches as of its cloning; without a connection those do.
        git("fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        new = not exists(f"refs/heads/{branch}") and not exists(f"refs/remotes/origin/{branch}")
        switched = git("switch", "-c", branch) if new else git("switch", branch)
        if switched.returncode != 0:
            raise CloneFailed(switched.stderr.strip()[-ERROR_CHARACTERS:] or "git switch failed")
        if new:
            pushed = git(*_push(branch))
            if pushed.returncode != 0:
                raise CloneFailed(pushed.stderr.strip()[-ERROR_CHARACTERS:] or "git push failed")

    def start(
        self, project: str, task: Task, url: str, then: Callable[[], None] | None = None
    ) -> None:
        """Clone in the background; `then` runs once the clone succeeded."""
        key = (project, task.slug)
        self._store.clone_started(*key)
        thread = threading.Thread(
            target=self._clone,
            args=(key, url, task.branch, task.base, Path(task.path), task.repo, then),
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
        self,
        key: tuple[str, str],
        url: str,
        branch: str,
        base: str,
        task_dir: Path,
        repo: str,
        then: Callable[[], None] | None,
    ) -> None:
        # Clone beside the final place and rename: a directory named `repo` is always complete.
        temporary = task_dir / f"{repo}.cloning"
        error = ""
        try:
            shutil.rmtree(temporary, ignore_errors=True)
            with self._mirror_held(key):
                mirror = self._update_mirror(key, url, task_dir.parent / METADATA_DIR / MIRROR)
                self._run(key, "clone", "--", str(mirror), str(temporary))
            self._run(key, "-C", str(temporary), "remote", "set-url", "origin", url)
            if branch or base:

                def on_remote(name: str) -> bool:
                    ref = f"refs/remotes/origin/{name}"
                    verify = ("-C", str(temporary), "rev-parse", "--verify", "--quiet", ref)
                    return self._run(key, *verify, check=False) == 0

                new = bool(branch) and not on_remote(branch)
                self._run(key, "-C", str(temporary), *_switch(branch, base, on_remote))
                if new:
                    self._run(key, "-C", str(temporary), *_push(branch))
            if self._run(key, "lfs", "version", check=False) == 0:
                self._run(key, "-C", str(temporary), "lfs", "pull")
            temporary.rename(task_dir / repo)
        except (CloneFailed, OSError) as failure:
            error = str(failure) or "The clone failed."
            shutil.rmtree(temporary, ignore_errors=True)
        finally:
            with self._lock:
                self._threads.pop(key, None)
                self._cancelled.discard(key)
            self._store.clone_finished(*key, error)
        if then is not None and not error:
            then()

    @contextlib.contextmanager
    def _mirror_held(self, key: tuple[str, str]) -> Iterator[None]:
        """Hold the project's mirror; a task cancelled while it waits for it gives up."""
        with self._lock:
            lock = self._mirror_locks.setdefault(key[0], threading.Lock())
        while not lock.acquire(timeout=0.05):
            with self._lock:
                if key in self._cancelled:
                    raise CloneFailed("The clone was cancelled.")
        try:
            yield
        finally:
            lock.release()

    def _update_mirror(self, key: tuple[str, str], url: str, mirror: Path) -> Path:
        """Create the project's mirror of `url`, or bring the one there up to date."""
        if mirror.exists():
            self._run(key, "-C", str(mirror), "fetch", "--prune", "origin")
            return mirror
        temporary = mirror.with_name(f"{MIRROR}.cloning")
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            self._run(key, "clone", "--bare", "--", url, str(temporary))
            # Branches only (their tags follow): every ref would take GitHub's pull requests too.
            self._run(
                key,
                *("-C", str(temporary)),
                *("config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"),
            )
            temporary.rename(mirror)
        except (CloneFailed, OSError):
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return mirror

    def _github_env(self, project: str) -> dict[str, str]:
        try:
            directory = Path(self._store.project(project).path)
        except ProjectNotFound:
            directory = None
        return project_github_env(self._store.root, directory)

    def _run(self, key: tuple[str, str], *arguments: str, check: bool = True) -> int:
        with self._lock:
            # A clone cancelled between two commands, or while it waited for the mirror,
            # must not start another: nothing would end it.
            if key in self._cancelled:
                raise CloneFailed("The clone was cancelled.")
            process = subprocess.Popen(
                [*self._git, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                # Fail instead of asking; as the GitHub account the project's config names;
                # Git LFS files are left to `git lfs pull`, the mirror has none to check out.
                env={
                    **os.environ,
                    **self._github_env(key[0]),
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_LFS_SKIP_SMUDGE": "1",
                },
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


def _switch(branch: str, base: str, on_remote: Callable[[str], bool]) -> list[str]:
    """How a fresh clone gets onto the task's branch: origin's if it has one, else a new one
    from `base`, or from the default branch without one. A base alone is checked out."""
    if not branch:
        return ["switch", base]
    if on_remote(branch):
        return ["switch", branch]
    if base:
        # Without tracking: the new branch is pushed under its own name, not into the base.
        return ["switch", "--no-track", "-c", branch, f"origin/{base}"]
    return ["switch", "-c", branch]


def _push(branch: str) -> list[str]:
    """How a branch the task created reaches origin, which its clone then tracks."""
    ref = f"refs/heads/{branch}"
    return ["push", "--quiet", "--set-upstream", "origin", f"{ref}:{ref}"]
