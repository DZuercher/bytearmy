"""Checks for cloning a project's repository into a task, against a real git."""

from __future__ import annotations

import os
import shutil
import subprocess
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


def start(store: Store, cloner: Cloner, project: str, name: str = "a", **fields) -> Task:
    task = store.create_task(project, TaskCreate(name=name, **fields))
    cloner.start(project, task, store.project(project).github_url)
    return task


def finished(store: Store, project: str, slug: str = "a", timeout: float = 20) -> Task:
    """The task once its clone has ended, one way or the other."""
    deadline = time.monotonic() + timeout
    while (task := store.task(project, slug)).clone == "cloning":
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


def test_a_new_branch_can_start_from_another_branch(store, project):
    start(store, Cloner(store), project, branch="fix/feature", base="feature")
    repo = Path(finished(store, project).path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "fix/feature"
    assert git("rev-parse", "HEAD", cwd=repo) == git("rev-parse", "origin/feature", cwd=repo)
    assert (repo / "feature.txt").exists()
    # It is pushed under its own name, not into its base.
    assert "branch.fix/feature.merge" not in git("config", "--list", "--local", cwd=repo)


def test_a_branch_the_remote_has_ignores_the_base(store, project):
    start(store, Cloner(store), project, branch="main", base="feature")
    repo = Path(finished(store, project).path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "main"
    assert not (repo / "feature.txt").exists()


def test_a_base_without_a_branch_is_checked_out(store, project):
    start(store, Cloner(store), project, base="feature")
    repo = Path(finished(store, project).path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "feature"


def test_a_base_the_remote_does_not_have_fails_the_clone(store, project):
    start(store, Cloner(store), project, branch="fix", base="no-such-branch")
    task = finished(store, project)

    assert task.clone == "failed"
    assert task.clone_error


def test_the_branches_come_from_the_mirror_the_default_first(store, project):
    cloner = Cloner(store)
    assert cloner.branches(project) == []  # no mirror yet
    start(store, cloner, project)
    finished(store, project)

    branches = cloner.branches(project)
    assert branches[0] == "main"
    assert sorted(branches) == ["feature", "main"]


def test_a_failed_clone_is_reported_and_leaves_nothing_behind(store, tmp_path):
    missing = tmp_path / "no-such-repository.git"
    project = store.create_project(ProjectMeta(name="Broken", github_url=str(missing))).slug

    start(store, Cloner(store), project)
    task = finished(store, project)

    assert task.clone == "failed"
    assert "no-such-repository.git" in task.clone_error
    assert entries(task.path) == [".bytearmy"]
    assert entries(store.root / project / ".bytearmy") == ["project.json"]


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


def test_git_is_asked_to_mirror_the_url_into_a_directory_beside_the_final_one(
    store, project, remote, tmp_path
):
    asked = clone_error_with(store, project, fake_git(tmp_path, 'echo "$*" >&2; exit 1'))

    mirror = store.root / project / ".bytearmy" / "mirror.git.cloning"
    assert asked == f"clone --bare -- {remote} {mirror}"


def test_the_clone_of_a_task_pulls_from_the_url_not_from_the_mirror(store, project, remote):
    start(store, Cloner(store), project)
    repo = Path(finished(store, project).path) / "widgets"

    assert git("remote", "get-url", "origin", cwd=repo) == str(remote)


def test_tasks_share_the_objects_of_the_mirror(store, project):
    start(store, Cloner(store), project)
    repo = Path(finished(store, project).path) / "widgets"
    mirror = store.root / project / ".bytearmy" / "mirror.git"

    def inodes(objects: Path) -> set[int]:
        return {path.stat().st_ino for path in objects.rglob("*") if path.is_file()}

    assert inodes(repo / ".git" / "objects") & inodes(mirror / "objects")


def test_a_later_task_sees_what_was_pushed_since_the_mirror_was_made(store, project, remote):
    cloner = Cloner(store)
    start(store, cloner, project)
    finished(store, project)
    git("branch", "pushed-later", "feature", cwd=remote)
    git("branch", "-D", "feature", cwd=remote)

    start(store, cloner, project, name="b", branch="pushed-later")
    repo = Path(finished(store, project, "b").path) / "widgets"

    assert git("branch", "--show-current", cwd=repo) == "pushed-later"
    assert (repo / "feature.txt").exists()
    assert git("branch", "-r", cwd=repo).split() == [
        "origin/HEAD",
        "->",
        "origin/main",
        "origin/main",
        "origin/pushed-later",
    ]


def test_the_mirror_takes_branches_only(store, project, remote):
    cloner = Cloner(store)
    start(store, cloner, project)
    finished(store, project)
    git("update-ref", "refs/pull/1/head", "feature", cwd=remote)

    start(store, cloner, project, name="b")
    finished(store, project, "b")

    mirror = store.root / project / ".bytearmy" / "mirror.git"
    assert git("for-each-ref", "--format=%(refname)", "refs/pull", cwd=mirror) == ""


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


def test_what_an_interrupted_mirror_left_behind_is_replaced(store, project):
    leftover = store.root / project / ".bytearmy" / "mirror.git.cloning"
    leftover.mkdir()
    (leftover / "half-a-mirror").write_text("junk")

    start(store, Cloner(store), project)

    assert finished(store, project).clone == "ready"
    assert entries(store.root / project / ".bytearmy") == ["mirror.git", "project.json"]


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


def test_tasks_started_together_wait_for_each_other_at_the_mirror(store, project, gated_git):
    cloner = Cloner(store, git=gated_git.command)
    start(store, cloner, project)
    gated_git.wait_until_running()
    start(store, cloner, project, name="b")

    gated_git.release()

    assert (finished(store, project).clone_error, finished(store, project, "b").clone_error) == (
        "",
        "",
    )


def test_a_failed_mirror_leaves_nothing_behind(store, project, tmp_path):
    leaving = fake_git(tmp_path, 'for last; do :; done; mkdir -p "$last"; exit 1')

    clone_error_with(store, project, leaving)

    assert entries(store.root / project / ".bytearmy") == ["project.json"]


def test_a_task_waiting_for_the_mirror_is_cancelled_at_once(store, project, gated_git):
    cloner = Cloner(store, git=gated_git.command)
    start(store, cloner, project)
    gated_git.wait_until_running()
    start(store, cloner, project, name="b")

    began = time.monotonic()
    cloner.cancel(project, "b")

    assert time.monotonic() - began < 2
    assert store.task(project, "b").clone_error == "The clone was cancelled."
    assert store.task(project, "a").clone == "cloning"
    gated_git.release()
    assert finished(store, project).clone == "ready"


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


@pytest.fixture
def lfs_project(store, tmp_path, monkeypatch):
    """A project whose repository keeps `big.bin` in Git LFS; git-lfs set up as `git lfs install`
    would, so cloning it smudges."""
    config = tmp_path / "gitconfig"
    config.write_text(
        '[filter "lfs"]\n'
        "\tclean = git-lfs clean -- %f\n"
        "\tsmudge = git-lfs smudge -- %f\n"
        "\tprocess = git-lfs filter-process\n"
        "\trequired = true\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    environment = {**os.environ, "GIT_CONFIG_SYSTEM": os.devnull}
    bare = tmp_path / "assets.git"
    work = tmp_path / "assets"
    for command, cwd in [
        (["init", "--bare", "-b", "main", str(bare)], tmp_path),
        (["init", "-b", "main", str(work)], tmp_path),
        (["lfs", "track", "*.bin"], work),
    ]:
        subprocess.run(["git", *command], cwd=cwd, env=environment, check=True)
    (work / "big.bin").write_bytes(b"large content\n")
    for command in [
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=t@example.org", "commit", "-m", "lfs"],
        ["push", f"file://{bare}", "main"],
    ]:
        subprocess.run(["git", *command], cwd=work, env=environment, check=True)
    return store.create_project(ProjectMeta(name="Assets", github_url=f"file://{bare}")).slug


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
def test_files_in_git_lfs_are_downloaded_from_the_url(store, lfs_project):
    cloner = Cloner(store)
    for name in ("a", "b"):  # the second task clones from a mirror that exists already
        start(store, cloner, lfs_project, name=name)
        task = finished(store, lfs_project, name)

        assert (task.clone, task.clone_error) == ("ready", "")
        assert (Path(task.path) / "assets" / "big.bin").read_bytes() == b"large content\n"
