"""Checks for `bytearmy merge`: merging a branch of origin into a task's branch, with git."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import git
from test_agents import calling_pane
from test_api_panes import serving
from test_prs import cloned_task

from bytearmy.config import AgentSettings, MasterSettings
from bytearmy.store import Store
from bytearmy.workspaces import Workspaces

MERGE_COMMANDS = ["merge.start", "merge.continue", "merge.abort"]


@pytest.fixture
def live(tmp_path, monkeypatch):
    # The merge commits git makes in the core need a name and an email.
    for variable in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{variable}_NAME", "Test")
        monkeypatch.setenv(f"{variable}_EMAIL", "test@example.org")
    store = Store(tmp_path / "root")
    workspaces = Workspaces(store, shell=["/bin/sh"])

    def run(remote: Path, **fields):
        clone = cloned_task(store, remote, **fields)
        return serving(store, workspaces), clone

    return run


def push_to(remote: Path, branch: str, name: str, text: str, tmp_path: Path) -> None:
    """Commit `text` as file `name` on origin's `branch`, as someone else would."""
    other = tmp_path / "other"
    if not other.exists():
        git("clone", str(remote), str(other), cwd=tmp_path)
    git("fetch", "origin", cwd=other)
    git("switch", "--force-create", branch, f"origin/{branch}", cwd=other)
    (other / name).write_text(text)
    git("add", ".", cwd=other)
    git("commit", "-m", f"change {name}", cwd=other)
    git("push", "origin", branch, cwd=other)


def commit_in(clone: Path, name: str, text: str) -> None:
    (clone / name).write_text(text)
    git("add", ".", cwd=clone)
    git("commit", "-m", f"change {name}", cwd=clone)


def parents(clone: Path) -> list[str]:
    return git("log", "-1", "--format=%P", cwd=clone).split()


def merging(clone: Path) -> bool:
    return (clone / ".git" / "MERGE_HEAD").exists()


def test_a_clean_merge_of_the_default_branch_is_committed(live, remote, bytearmy, tmp_path):
    serve, clone = live(remote, branch="fix/login")
    commit_in(clone, "login.txt", "fixed\n")
    push_to(remote, "main", "news.txt", "from main\n", tmp_path)
    with serve as core:
        me = calling_pane(core)

        code, said = bytearmy(me, "merge", "start")

        assert code == 0, said
        assert (clone / "news.txt").read_text() == "from main\n"
        assert len(parents(clone)) == 2
        assert not merging(clone)
        assert git("status", "--porcelain", cwd=clone) == ""


def test_a_named_branch_is_merged(live, remote, bytearmy):
    serve, clone = live(remote, branch="fix/login")
    commit_in(clone, "login.txt", "fixed\n")
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "merge", "start", "feature")[0] == 0
    assert (clone / "feature.txt").read_text() == "only on feature\n"
    assert len(parents(clone)) == 2


def test_without_a_branch_the_tasks_base_is_merged(live, remote, bytearmy, tmp_path):
    serve, clone = live(remote, branch="fix/login", base="feature")
    push_to(remote, "feature", "later.txt", "later on feature\n", tmp_path)
    push_to(remote, "main", "news.txt", "from main\n", tmp_path)
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "merge", "start")[0] == 0
    assert (clone / "later.txt").exists()
    assert not (clone / "news.txt").exists()


def test_conflicts_are_listed_and_the_merge_continues_once_resolved(
    live, remote, bytearmy, tmp_path
):
    serve, clone = live(remote, branch="fix/login")
    commit_in(clone, "README.md", "hello from the task\n")
    push_to(remote, "main", "README.md", "hello from main\n", tmp_path)
    with serve as core:
        me = calling_pane(core)

        code, said = bytearmy(me, "merge", "start")

        assert code == 0
        assert said.startswith("merging origin/main stopped with conflicts in:\n  README.md\n")
        assert "bytearmy merge continue" in said
        assert merging(clone)

        code, refused = bytearmy(me, "merge", "continue")
        assert code == 1
        assert refused == (
            "bytearmy: these files are still in conflict; resolve and `git add` them:\n  README.md"
        )
        assert merging(clone)

        (clone / "README.md").write_text("hello from both\n")
        git("add", "README.md", cwd=clone)
        assert bytearmy(me, "merge", "continue")[0] == 0

    assert not merging(clone)
    assert len(parents(clone)) == 2
    assert (clone / "README.md").read_text() == "hello from both\n"


def test_abort_undoes_the_merge(live, remote, bytearmy, tmp_path):
    serve, clone = live(remote, branch="fix/login")
    commit_in(clone, "README.md", "hello from the task\n")
    before = git("rev-parse", "HEAD", cwd=clone)
    push_to(remote, "main", "README.md", "hello from main\n", tmp_path)
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "merge", "start")[0] == 0

        assert bytearmy(me, "merge", "abort") == (0, "aborted the merge")

        assert not merging(clone)
        assert git("rev-parse", "HEAD", cwd=clone) == before
        assert (clone / "README.md").read_text() == "hello from the task\n"
        for action in ("continue", "abort"):
            assert bytearmy(me, "merge", action) == (1, "bytearmy: no merge is in progress")


def test_what_git_says_when_it_fails_reaches_the_agent(live, remote, bytearmy):
    serve, _ = live(remote, branch="fix/login")
    with serve as core:
        me = calling_pane(core)
        code, said = bytearmy(me, "merge", "start", "no-such-branch")
    assert code == 1
    assert said.startswith("bytearmy: `git fetch` failed: ")


def test_every_agent_may_merge():
    assert AgentSettings().denied(MERGE_COMMANDS) == []
    assert MasterSettings().denied(MERGE_COMMANDS) == []
    assert "Bash(bytearmy merge *)" in AgentSettings().granted_tools()
    assert MasterSettings(allow=["merge.*"]).granted_tools() == ["Bash(bytearmy merge *)"]
    assert AgentSettings(allow=["pr.*"]).denied(MERGE_COMMANDS) == MERGE_COMMANDS
