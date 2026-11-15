"""Checks for finding the pull request of a task's branch, and the webpage tool offering it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import wait_until

from bytearmy.models import PaneOpen, ProjectMeta, TaskCreate
from bytearmy.pulls import PullRequests, pull_request_number
from bytearmy.store import Store
from bytearmy.workspaces import Workspaces

PAGE = "https://github.com/org/widgets"
LISTING = (
    "aaa\trefs/heads/feature\n"
    "bbb\trefs/pull/3/head\n"
    "aaa\trefs/pull/5/head\n"
    "aaa\trefs/pull/12/head\n"
    "ccc\trefs/pull/20/head\n"
)


def test_the_pull_request_of_a_branch_is_the_newest_one_at_its_commit():
    assert pull_request_number(LISTING, "feature") == 12
    assert pull_request_number(LISTING, "other") is None
    assert pull_request_number("aaa\trefs/heads/feature\n", "feature") is None


def fake_git(tmp_path: Path, listing: str, fails: bool = False) -> list[str]:
    """A git whose `ls-remote` prints `listing`, and that writes down how often it ran."""
    script = tmp_path / "git.py"
    calls = tmp_path / "calls"
    script.write_text(
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write({listing!r})\n"
        f"sys.exit({1 if fails else 0})\n"
    )
    return [sys.executable, str(script)]


def calls(tmp_path: Path) -> list[str]:
    file = tmp_path / "calls"
    return file.read_text().splitlines() if file.exists() else []


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "root")
    project = store.create_project(ProjectMeta(name="Widgets", github_url=f"{PAGE}.git")).slug
    store.create_task(project, TaskCreate(name="Feature", branch="feature"))
    store.create_task(project, TaskCreate(name="Plain"))
    for task in ("feature", "plain"):
        store.start_task(project, task)
    return store


def test_a_page_is_looked_up_in_the_background_and_counts_as_a_change(tmp_path, store):
    pulls = PullRequests(store, git=fake_git(tmp_path, LISTING))
    project = store.project("widgets")
    task = store.task("widgets", "feature")
    version = store.version

    assert pulls.page(project, task) == ""  # not known yet
    wait_until(lambda: store.version > version, "the lookup")
    assert pulls.page(project, task) == f"{PAGE}/pull/12"
    assert len(calls(tmp_path)) == 1  # still fresh: the remote is not asked again
    assert calls(tmp_path)[0].startswith(f"ls-remote -- {PAGE}.git refs/heads/feature")


def test_waiting_looks_the_page_up_before_answering(tmp_path, store):
    pulls = PullRequests(store, git=fake_git(tmp_path, LISTING))

    page = pulls.page(store.project("widgets"), store.task("widgets", "feature"), wait=True)

    assert page == f"{PAGE}/pull/12"


def test_a_stale_page_is_looked_up_again(tmp_path, store):
    now = [0.0]
    pulls = PullRequests(store, git=fake_git(tmp_path, LISTING), clock=lambda: now[0])
    project, task = store.project("widgets"), store.task("widgets", "feature")
    pulls.page(project, task, wait=True)

    now[0] = 1000.0
    pulls.page(project, task, wait=True)

    assert len(calls(tmp_path)) == 2


def test_a_task_without_a_branch_and_a_failing_remote_have_no_page(tmp_path, store):
    pulls = PullRequests(store, git=fake_git(tmp_path, LISTING, fails=True))
    project = store.project("widgets")

    assert pulls.page(project, store.task("widgets", "plain"), wait=True) == ""
    assert calls(tmp_path) == []  # nothing to ask without a branch
    assert pulls.page(project, store.task("widgets", "feature"), wait=True) == ""


def test_the_webpage_tool_defaults_to_the_pull_request_of_the_task(tmp_path, store):
    workspaces = Workspaces(
        store, shell=["/bin/sh"], pulls=PullRequests(store, git=fake_git(tmp_path, LISTING))
    )
    try:
        offered = {tool.id: tool for tool in workspaces.tools("widgets", "feature")}
        wait_until(lambda: calls(tmp_path), "the lookup")
        wait_until(
            lambda: (
                {t.id: t for t in workspaces.tools("widgets", "feature")}["webpage"]
                .parameters[0]
                .default
                == f"{PAGE}/pull/12"
            ),
            "the pull request to be offered",
        )
        assert offered["webpage"].parameters[0].default == PAGE  # until the lookup is done
        plain = {tool.id: tool for tool in workspaces.tools("widgets", "plain")}
        assert plain["webpage"].parameters[0].default == PAGE

        pane = workspaces.open_pane("widgets", "feature", PaneOpen(tool="webpage"))
        assert pane.values == {"url": f"{PAGE}/pull/12"}
    finally:
        workspaces.shutdown()
