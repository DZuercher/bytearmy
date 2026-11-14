"""Checks for cloning a project's repository into a task, against a real git."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import git

from bytearmy.clones import Cloner
from bytearmy.models import ProjectMeta, Task, TaskCreate
from bytearmy.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def project(store, remote):
    return store.create_project(ProjectMeta(name="Widgets", github_url=str(remote))).slug


def start(store: Store, cloner: Cloner, project: str, **fields) -> Task:
    task = store.create_task(project, TaskCreate(name="a", **fields))
    cloner.start(project, task, store.project(project).github_url)
    return task


def finished(store: Store, project: str, timeout: float = 20) -> Task:
    """The task once its clone has ended, one way or the other."""
    deadline = time.monotonic() + timeout
    while (task := store.task(project, "a")).clone == "cloning":
        assert time.monotonic() < deadline, "the clone did not end"
        time.sleep(0.02)
    return task


def entries(directory: str | Path) -> list[str]:
    return sorted(entry.name for entry in Path(directory).iterdir())


def test_clone_lands_in_the_task_directory_on_the_default_branch(store, project):
    start(store, Cloner(store), project)
    task = finished(store, project)

    assert (task.clone, task.clone_error) == ("ready", "")
    assert entries(task.path) == [".bytearmy", "widgets"]
    assert (Path(task.path) / "widgets" / "README.md").read_text() == "hello\n"
    assert git("branch", "--show-current", cwd=Path(task.path) / "widgets") == "main"


def test_a_branch_that_exists_on_the_remote_is_checked_out(store, project):
    start(store, Cloner(store), project, branch="feature")
    repo = Path(finished(store, project).path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "feature"
    assert (repo / "feature.txt").exists()
    assert git("rev-parse", "--abbrev-ref", "feature@{upstream}", cwd=repo) == "origin/feature"


def test_a_branch_the_remote_does_not_have_is_created_from_the_default_branch(store, project):
    start(store, Cloner(store), project, branch="fix/login")
    repo = Path(finished(store, project).path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "fix/login"
    assert git("rev-parse", "HEAD", cwd=repo) == git("rev-parse", "origin/main", cwd=repo)
    assert not (repo / "feature.txt").exists()


def test_a_failed_clone_is_reported_and_leaves_nothing_behind(store, tmp_path):
    missing = tmp_path / "no-such-repository.git"
    project = store.create_project(ProjectMeta(name="Broken", github_url=str(missing))).slug

    start(store, Cloner(store), project)
    task = finished(store, project)

    assert task.clone == "failed"
    assert "no-such-repository.git" in task.clone_error
    assert entries(task.path) == [".bytearmy"]


def fake_git(tmp_path: Path, body: str) -> list[str]:
    """A stand-in for git: a shell script with the given body."""
    script = tmp_path / "fake-git"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return [str(script)]


def clone_error_with(store: Store, project: str, git: list[str]) -> str:
    start(store, Cloner(store, git=git), project)
    task = finished(store, project)
    assert task.clone == "failed"
    return task.clone_error


def test_git_is_asked_to_clone_the_url_into_a_directory_beside_the_final_one(
    store, project, remote, tmp_path
):
    asked = clone_error_with(store, project, fake_git(tmp_path, 'echo "$*" >&2; exit 1'))

    assert asked == f"clone -- {remote} {store.root / project / 'a' / 'widgets.cloning'}"


def test_git_must_not_prompt(store, project, tmp_path):
    git = fake_git(tmp_path, 'echo "prompt=$GIT_TERMINAL_PROMPT" >&2; exit 1')

    assert clone_error_with(store, project, git) == "prompt=0"


def test_a_failure_without_a_complaint_still_has_a_message(store, project, tmp_path):
    assert clone_error_with(store, project, fake_git(tmp_path, "exit 3")) == "The clone failed."


def test_only_the_end_of_a_long_complaint_is_kept(store, project, tmp_path):
    git = fake_git(tmp_path, "printf 'x%.0s' $(seq 1 600) >&2; printf END >&2; exit 1")

    kept = clone_error_with(store, project, git)

    assert (len(kept), kept[-4:]) == (500, "xEND")


def test_a_git_that_cannot_be_run_is_reported(store, project, tmp_path):
    missing = str(tmp_path / "no-git-here")

    assert missing in clone_error_with(store, project, [missing])


def test_a_step_failing_after_the_clone_is_reported_and_leaves_nothing_behind(
    store, project, tmp_path
):
    refusing = fake_git(
        tmp_path,
        'case " $* " in *" switch "*) echo "switch refused" >&2; exit 1 ;; esac\nexec git "$@"',
    )

    start(store, Cloner(store, git=refusing), project, branch="feature")
    task = finished(store, project)

    assert (task.clone, task.clone_error) == ("failed", "switch refused")
    assert entries(task.path) == [".bytearmy"]


def test_what_an_interrupted_clone_left_behind_is_replaced(store, project):
    task = store.create_task(project, TaskCreate(name="a"))
    leftover = Path(task.path) / "widgets.cloning"
    leftover.mkdir()
    (leftover / "half-a-clone").write_text("junk")

    Cloner(store).start(project, task, store.project(project).github_url)

    assert finished(store, project).clone == "ready"
    assert entries(task.path) == [".bytearmy", "widgets"]


def test_a_task_is_cloning_until_git_is_done(store, project, gated_git):
    start(store, Cloner(store, git=gated_git.command), project)
    gated_git.wait_until_running()

    assert store.task(project, "a").clone == "cloning"

    gated_git.release()
    assert finished(store, project).clone == "ready"


def test_cancel_stops_the_clone_of_a_task(store, project, gated_git):
    cloner = Cloner(store, git=gated_git.command)
    task = start(store, cloner, project)
    gated_git.wait_until_running()

    cloner.cancel(project, "a")

    assert not gated_git.running()
    assert (store.task(project, "a").clone, store.task(project, "a").clone_error) == (
        "failed",
        "The clone was cancelled.",
    )
    assert entries(task.path) == [".bytearmy"]


def test_cancel_leaves_the_clones_of_other_tasks_alone(store, project, gated_git):
    cloner = Cloner(store, git=gated_git.command)
    start(store, cloner, project)
    gated_git.wait_until_running()

    cloner.cancel(project, "another-task")
    cloner.cancel("another-project")

    assert gated_git.running()
    assert store.task(project, "a").clone == "cloning"


def test_shutdown_stops_every_clone(store, project, gated_git):
    cloner = Cloner(store, git=gated_git.command)
    start(store, cloner, project)
    gated_git.wait_until_running()

    cloner.shutdown()

    assert not gated_git.running()
    assert store.task(project, "a").clone == "failed"


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("feature", True),
        ("fix/login", True),
        ("bad..name", False),
        ("-x", False),
        ("with space", False),
    ],
)
def test_valid_branch(store, name, valid):
    assert Cloner(store).valid_branch(name) is valid
