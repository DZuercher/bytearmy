"""The pull request of a task's branch, created, shown and merged with gh for an agent.

Claude's auto mode often keeps an agent from running `gh pr create` or `gh pr merge`.
`bytearmy pr ...` does it instead, and one narrow rule lets Claude run it. It reaches no
further than the task: the branch is always the task's own, in the task's clone, as the
GitHub account of the project.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

TIMEOUT_SECONDS = 120.0
MERGE_METHODS = ("merge", "squash", "rebase")


class GitHubFailed(RuntimeError):
    """git or gh failed in a task's clone, or the task lacks what it needs; the message
    says why."""


class PullRequestFailed(GitHubFailed):
    """git or gh failed, or the task cannot have a pull request; the message says why."""


class PullRequestCommands:
    """Runs git and gh in a task's clone; both programs can be replaced for tests."""

    def __init__(self, git: Sequence[str] = ("git",), gh: Sequence[str] = ("gh",)) -> None:
        self._git = list(git)
        self._gh = list(gh)

    def create(
        self,
        clone: Path,
        branch: str,
        env: Mapping[str, str],
        title: str,
        body: str,
        base: str = "",
        draft: bool = False,
    ) -> str:
        """Push the branch, then open its pull request; gives the pull request's URL."""
        ref = f"refs/heads/{branch}"
        push = [*self._git, "push", "--set-upstream", "origin", f"{ref}:{ref}"]
        self._run(push, clone, env, "git push")
        command = [*self._gh, "pr", "create", "--head", branch, "--title", title, "--body", body]
        if base:
            command += ["--base", base]
        if draft:
            command.append("--draft")
        return self._run(command, clone, env, "gh pr create").strip()

    def view(self, clone: Path, branch: str, env: Mapping[str, str]) -> str:
        """What gh shows of the branch's pull request."""
        return self._run([*self._gh, "pr", "view", branch], clone, env, "gh pr view").rstrip("\n")

    def merge(
        self,
        clone: Path,
        branch: str,
        env: Mapping[str, str],
        method: str = "squash",
        delete_branch: bool = False,
    ) -> str:
        """Merge the branch's pull request."""
        if method not in MERGE_METHODS:
            choices = ", ".join(MERGE_METHODS)
            raise PullRequestFailed(f"unknown merge method {method!r}: one of {choices}")
        command = [*self._gh, "pr", "merge", branch, f"--{method}"]
        if delete_branch:
            command.append("--delete-branch")
        output = self._run(command, clone, env, "gh pr merge").strip()
        return output or f"merged the pull request of {branch}"

    def _run(self, command: list[str], clone: Path, env: Mapping[str, str], label: str) -> str:
        return run_in_clone(command, clone, env, label, PullRequestFailed)


def run_in_clone(
    command: list[str],
    clone: Path,
    env: Mapping[str, str],
    label: str,
    failed: type[GitHubFailed] = GitHubFailed,
) -> str:
    """Run a command in the clone and give what it printed; `label` names it, and `failed`
    is raised with what it said when it fails."""
    try:
        done = subprocess.run(
            command,
            cwd=clone,
            check=False,  # a failure is told by what it said
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={  # fail instead of asking
                **os.environ,
                **env,
                "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1",
            },
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise failed(f"`{label}` could not run: {error}") from error
    if done.returncode != 0:
        said = done.stderr.strip() or done.stdout.strip() or f"exit code {done.returncode}"
        raise failed(f"`{label}` failed: {said}")
    return done.stdout
