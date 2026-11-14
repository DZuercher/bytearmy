"""Helpers shared by the checks: reading terminals, a repository to clone from, a git on hold."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

# The fixture repositories must not depend on the git configuration of the machine.
ISOLATED = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


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
