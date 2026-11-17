"""Merging a branch of origin into a task's branch, with git for an agent.

Claude's auto mode often keeps an agent from running `git merge`. `bytearmy merge ...` does
it instead, and one narrow rule lets Claude run it. It reaches no further than the task: git
runs in the task's clone, fetches as the GitHub account of the project, and merges into
whatever the clone has checked out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from bytearmy.prs import GitHubFailed, run_in_clone


class MergeFailed(GitHubFailed):
    """git failed, or the task has no clone to merge in; the message says why."""


class MergeCommands:
    """Runs git in a task's clone; the program can be replaced for tests."""

    def __init__(self, git: Sequence[str] = ("git",)) -> None:
        self._git = list(git)

    def start(self, clone: Path, env: Mapping[str, str], branch: str) -> str:
        """Fetch origin's `branch` and merge it; a clean merge is committed, a merge with
        conflicts stops and names the conflicted files."""
        remote = f"refs/remotes/origin/{branch}"
        fetch = [*self._git, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:{remote}"]
        self._run(fetch, clone, env, "git fetch")
        try:
            merged = self._run(
                [*self._git, "merge", "--no-edit", f"origin/{branch}"], clone, env, "git merge"
            )
        except MergeFailed:
            conflicted = self._conflicted(clone, env)
            if not conflicted:
                raise
            listed = "\n".join(f"  {path}" for path in conflicted)
            return (
                f"merging origin/{branch} stopped with conflicts in:\n{listed}\n"
                "Resolve them, `git add` the files, have the user review the resolution with"
                " `bytearmy review --uncommitted`, then commit the merge with"
                " `bytearmy merge continue`; `bytearmy merge abort` undoes the merge."
            )
        return merged.strip() or f"merged origin/{branch}"

    def resume(self, clone: Path, env: Mapping[str, str]) -> str:
        """Commit the merge once no file is left in conflict."""
        self._merging(clone, env)
        conflicted = self._conflicted(clone, env)
        if conflicted:
            listed = "\n".join(f"  {path}" for path in conflicted)
            raise MergeFailed(
                f"these files are still in conflict; resolve and `git add` them:\n{listed}"
            )
        committed = self._run([*self._git, "commit", "--no-edit"], clone, env, "git commit")
        return committed.strip() or "committed the merge"

    def abort(self, clone: Path, env: Mapping[str, str]) -> str:
        """Undo the merge: the clone is back where it was before it started."""
        self._merging(clone, env)
        self._run([*self._git, "merge", "--abort"], clone, env, "git merge --abort")
        return "aborted the merge"

    def _merging(self, clone: Path, env: Mapping[str, str]) -> None:
        try:
            self._run(
                [*self._git, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"], clone, env, "git"
            )
        except MergeFailed:
            raise MergeFailed("no merge is in progress") from None

    def _conflicted(self, clone: Path, env: Mapping[str, str]) -> list[str]:
        listed = self._run(
            [*self._git, "diff", "--name-only", "--diff-filter=U"], clone, env, "git diff"
        )
        return listed.splitlines()

    def _run(self, command: list[str], clone: Path, env: Mapping[str, str], label: str) -> str:
        # The editor git might open for a message is never there to close.
        return run_in_clone(command, clone, {**env, "GIT_EDITOR": "true"}, label, MergeFailed)
