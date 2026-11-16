"""The GitHub Actions workflows of a task's repository, listed and dispatched with gh.

Claude's auto mode often keeps an agent from running `gh workflow run`. `bytearmy workflow
...` does it instead, and one narrow rule lets Claude run it. It reaches no further than the
task: gh runs in the task's clone, as the GitHub account of the project, and a dispatch runs
on the task's branch unless the agent names another ref.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from bytearmy.prs import GitHubFailed, run_in_clone

DEFAULT_RUN_LIMIT = 10


class WorkflowFailed(GitHubFailed):
    """gh failed, or the task has no clone to find workflows in; the message says why."""


class WorkflowCommands:
    """Runs gh in a task's clone; the program can be replaced for tests."""

    def __init__(self, gh: Sequence[str] = ("gh",)) -> None:
        self._gh = list(gh)

    def list(self, clone: Path, env: Mapping[str, str]) -> str:
        """The workflows of the repository, as gh lists them."""
        listed = self._run([*self._gh, "workflow", "list", "--all"], clone, env, "gh workflow list")
        return listed.rstrip("\n") or "the repository has no workflows"

    def run(
        self,
        clone: Path,
        env: Mapping[str, str],
        workflow: str,
        ref: str = "",
        inputs: Mapping[str, str] | None = None,
    ) -> str:
        """Dispatch a workflow (its name, ID or file name) on `ref` with these inputs."""
        command = [*self._gh, "workflow", "run", workflow]
        if ref:
            command += ["--ref", ref]
        for name, value in (inputs or {}).items():
            command += ["--raw-field", f"{name}={value}"]
        said = self._run(command, clone, env, "gh workflow run").strip()
        return said or f"dispatched {workflow}" + (f" on {ref}" if ref else "")

    def runs(
        self,
        clone: Path,
        env: Mapping[str, str],
        workflow: str = "",
        branch: str = "",
        limit: int = DEFAULT_RUN_LIMIT,
    ) -> str:
        """The latest runs, of one workflow and one branch if given."""
        command = [*self._gh, "run", "list", "--limit", str(limit)]
        if workflow:
            command += ["--workflow", workflow]
        if branch:
            command += ["--branch", branch]
        listed = self._run(command, clone, env, "gh run list").rstrip("\n")
        return listed or "no runs found"

    def _run(self, command: list[str], clone: Path, env: Mapping[str, str], label: str) -> str:
        return run_in_clone(command, clone, env, label, WorkflowFailed)
