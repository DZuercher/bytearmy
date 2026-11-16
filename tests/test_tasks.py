"""Checks for tasks in the file-system store."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from bytearmy.models import ProjectMeta, TaskCreate, TaskFile
from bytearmy.store import (
    INTERRUPTED_CLONE,
    Blocked,
    DuplicateName,
    InvalidEdit,
    InvalidOrder,
    Store,
    TaskNotFound,
    UnknownDependency,
    repo_directory,
)

TODAY = date(2026, 11, 14)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root", today=lambda: TODAY)


@pytest.fixture
def project(store):
    return store.create_project(ProjectMeta(name="Demo")).slug


def add(store: Store, project: str, name: str, **fields):
    return store.create_task(project, TaskCreate(name=name, **fields))


def bars(store: Store, project: str) -> tuple[list[str], list[str]]:
    """The slugs in the backlog and in the task bar."""
    found = store.project(project)
    return [task.slug for task in found.backlog], [task.slug for task in found.tasks]


def task_file(store: Store, project: str, slug: str) -> dict:
    return json.loads((store.root / project / slug / ".bytearmy" / "task.json").read_text())


def plant_task(directory: Path, status: str = "Backlog", **fields) -> None:
    """A task directory that appeared without going through the store."""
    (directory / ".bytearmy").mkdir(parents=True)
    content = {"name": directory.name, "status": status, **fields}
    (directory / ".bytearmy" / "task.json").write_text(json.dumps(content))


# -- creating ---------------------------------------------------------------


def test_create_writes_task_metadata_into_a_slug_directory(store, project):
    add(store, project, "Design API")

    task = add(
        store,
        project,
        "Fix login",
        description="users cannot log in",
        branch="fix/login",
        reminder=date(2026, 12, 1),
        dependencies=["design-api"],
    )

    assert task.slug == "fix-login"
    assert Path(task.path) == store.root / project / "fix-login"
    assert task_file(store, project, "fix-login") == {
        "name": "Fix login",
        "description": "users cannot log in",
        "branch": "fix/login",
        "base": "",
        "reminder": "2026-12-01",
        "dependencies": ["design-api"],
        "status": "Backlog",
        "state": "backlog",
        "repo": "",
        "clone_error": "",
        "scratchpad": False,
    }


def test_new_task_is_last_in_the_backlog(store, project):
    for name in ("c", "a", "b"):
        add(store, project, name)

    assert bars(store, project) == (["c", "a", "b"], [])
    assert bars(Store(store.root), project) == (["c", "a", "b"], [])


@pytest.mark.parametrize("second", ["Fix", "  Fix "])
def test_a_second_task_with_the_same_name_is_refused(store, project, second):
    add(store, project, "Fix")
    before = sorted(entry.name for entry in (store.root / project).iterdir())

    with pytest.raises(DuplicateName):
        add(store, project, second)

    assert sorted(entry.name for entry in (store.root / project).iterdir()) == before


def test_the_same_task_name_is_fine_in_another_project(store, project):
    other = store.create_project(ProjectMeta(name="Other")).slug
    add(store, project, "Fix")

    assert add(store, other, "Fix").slug == "fix"


def test_task_names_with_the_same_slug_get_distinct_directories(store, project):
    (store.root / project / "Docs").mkdir()  # not a task, and differs only by case

    created = [add(store, project, name).slug for name in ("Fix", "fix", "docs", "!!!")]

    assert created == ["fix", "fix-2", "docs-2", "task"]


def test_dependencies_must_be_tasks_of_the_project(store, project):
    add(store, project, "a")

    with pytest.raises(UnknownDependency):
        add(store, project, "b", dependencies=["ghost"])
    assert bars(store, project) == (["a"], [])

    assert add(store, project, "c", dependencies=["a", "a"]).dependencies == ["a"]


@pytest.mark.parametrize(
    ("url", "directory"),
    [
        ("git@github.com:owner/widgets.git", "widgets"),
        ("git@github.com:widgets.git", "widgets"),
        ("https://github.com/owner/widgets", "widgets"),
        ("https://github.com/owner/widgets/", "widgets"),
        ("/srv/git/widgets.git", "widgets"),
        ("https://github.com/owner/..", "repo"),
    ],
)
def test_repo_directory(url, directory):
    assert repo_directory(url) == directory


def test_a_task_records_the_repo_directory_of_its_project(store, project):
    with_url = store.create_project(
        ProjectMeta(name="Widgets", github_url="git@github.com:owner/widgets.git")
    ).slug

    assert add(store, with_url, "a").repo == "widgets"
    assert add(store, project, "a").repo == ""


# -- starting ---------------------------------------------------------------


def test_starting_a_task_moves_it_to_the_end_of_the_task_bar_as_new(store, project):
    for name in ("a", "b", "c"):
        add(store, project, name)

    store.start_task(project, "b")
    store.start_task(project, "a")

    assert bars(store, project) == (["c"], ["b", "a"])
    assert bars(Store(store.root), project) == (["c"], ["b", "a"])
    assert [task.status for task in store.project(project).tasks] == ["New", "New"]
    assert store.project(project).backlog[0].status == "Backlog"


def test_starting_a_started_task_changes_nothing(store, project):
    add(store, project, "a")
    add(store, project, "b")
    store.start_task(project, "a")
    store.start_task(project, "b")

    store.start_task(project, "a")

    assert bars(store, project) == ([], ["a", "b"])


def test_a_task_waits_for_its_dependencies_in_the_backlog(store, project):
    add(store, project, "Design API")
    add(store, project, "Fix login", dependencies=["design-api"])

    with pytest.raises(Blocked) as blocked:
        store.start_task(project, "fix-login")
    assert blocked.value.args == ("Fix login", ["Design API"])
    assert bars(store, project) == (["design-api", "fix-login"], [])
    assert store.task(project, "fix-login").blocked_by == ["Design API"]

    store.start_task(project, "design-api")
    assert store.task(project, "fix-login").blocked_by == []
    store.start_task(project, "fix-login")
    assert bars(store, project) == ([], ["design-api", "fix-login"])


def test_a_dependency_that_no_longer_exists_does_not_block(store, project):
    plant_task(store.root / project / "orphan", dependencies=["ghost"])

    assert store.task(project, "orphan").blocked_by == []
    store.start_task(project, "orphan")
    assert bars(store, project) == ([], ["orphan"])


# -- deleting ---------------------------------------------------------------


def test_delete_removes_the_task_directory_and_what_pointed_at_it(store, project):
    add(store, project, "a")
    add(store, project, "b", dependencies=["a"])
    store.start_task(project, "a")
    store.focus_task(project, "a")
    (store.root / project / "a" / "notes.md").write_text("work")

    store.delete_task(project, "a")

    assert not (store.root / project / "a").exists()
    assert bars(store, project) == (["b"], [])
    assert task_file(store, project, "b")["dependencies"] == []
    assert store.project(project).focused_task is None


@pytest.mark.parametrize("slug", ["../../victim", "link", "plain"])
def test_delete_refuses_what_is_not_a_task_of_this_project(store, project, tmp_path, slug):
    victim = tmp_path / "victim"  # a real task directory, but outside the project
    plant_task(victim)
    (store.root / project / "link").symlink_to(victim, target_is_directory=True)
    (store.root / project / "plain").mkdir()
    (store.root / project / "plain" / "keep.txt").write_text("data")

    with pytest.raises(TaskNotFound):
        store.delete_task(project, slug)

    assert (victim / ".bytearmy" / "task.json").exists()
    assert (store.root / project / "plain" / "keep.txt").exists()


# -- ordering and focus -----------------------------------------------------


def test_each_bar_keeps_its_own_order(store, project):
    for name in ("a", "b", "c", "d"):
        add(store, project, name)
    store.start_task(project, "c")
    store.start_task(project, "d")

    store.reorder_tasks(project, "backlog", ["b", "a"])
    store.reorder_tasks(project, "tasks", ["d", "c"])

    assert bars(store, project) == (["b", "a"], ["d", "c"])
    assert bars(Store(store.root), project) == (["b", "a"], ["d", "c"])


@pytest.mark.parametrize(
    ("bar", "order"),
    [
        ("backlog", ["a"]),
        ("backlog", ["a", "b", "c"]),
        ("backlog", ["a", "a", "b"]),
        ("tasks", ["a", "b"]),
    ],
)
def test_reorder_rejects_anything_but_a_permutation_of_the_bar(store, project, bar, order):
    for name in ("a", "b", "c"):
        add(store, project, name)
    store.start_task(project, "c")

    with pytest.raises(InvalidOrder):
        store.reorder_tasks(project, bar, order)
    assert bars(store, project) == (["a", "b"], ["c"])


def test_tasks_the_order_does_not_know_go_to_the_bar_their_status_says(store, project):
    add(store, project, "a")
    plant_task(store.root / project / "zeta", status="Backlog")
    plant_task(store.root / project / "alpha", status="New")

    assert bars(store, project) == (["a", "zeta"], ["alpha"])


def test_directories_without_readable_task_metadata_are_not_tasks(store, project):
    add(store, project, "a")
    (store.root / project / "plain").mkdir()
    (store.root / project / "broken" / ".bytearmy").mkdir(parents=True)
    (store.root / project / "broken" / ".bytearmy" / "task.json").write_text("not json")

    assert bars(store, project) == (["a"], [])


def test_only_a_task_in_the_task_bar_can_be_focused(store, project):
    add(store, project, "a")
    add(store, project, "b")
    store.start_task(project, "a")

    with pytest.raises(TaskNotFound):
        store.focus_task(project, "ghost")

    store.focus_task(project, "a")
    assert Store(store.root).project(project).focused_task == "a"

    store.focus_task(project, "b")  # still in the backlog
    assert store.project(project).focused_task is None

    store.focus_task(project, "a")
    store.focus_task(project, None)
    assert store.project(project).focused_task is None


# -- reminder ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("reminder", "due"),
    [(date(2026, 11, 13), True), (TODAY, True), (date(2026, 11, 15), False), (None, False)],
)
def test_reminder_is_due_from_its_date_on(store, project, reminder, due):
    assert add(store, project, "a", reminder=reminder).reminder_due is due


# -- clone state ------------------------------------------------------------


def test_clone_state_follows_the_disk_and_the_running_clones(store, project):
    plant_task(store.root / project / "plain")
    plant_task(store.root / project / "cloned", repo="widgets")
    (store.root / project / "cloned" / "widgets" / ".git").mkdir(parents=True)
    plant_task(store.root / project / "unfinished", repo="widgets")

    def clone(slug):
        task = store.task(project, slug)
        return task.clone, task.clone_error

    assert clone("plain") == ("none", "")
    assert clone("cloned") == ("ready", "")
    assert clone("unfinished") == ("failed", INTERRUPTED_CLONE)

    store.clone_started(project, "unfinished")
    assert clone("unfinished") == ("cloning", "")
    assert clone("cloned") == ("ready", "")

    store.clone_finished(project, "unfinished", "remote not found")
    assert clone("unfinished") == ("failed", "remote not found")
    assert Store(store.root).task(project, "unfinished").clone_error == "remote not found"


def test_a_clone_may_finish_after_its_task_or_project_was_deleted(store, project):
    add(store, project, "a")
    store.clone_started(project, "a")
    store.delete_task(project, "a")

    store.clone_finished(project, "a", "cancelled")
    assert bars(store, project) == ([], [])

    add(store, project, "b")
    store.clone_started(project, "b")
    store.delete_project(project)

    store.clone_finished(project, "b", "cancelled")
    assert store.app_state().projects == []


# -- the scratchpad ---------------------------------------------------------


def test_a_scratchpad_is_added_once_and_comes_first_in_the_task_bar(store, project):
    add(store, project, "A")
    store.start_task(project, "a")

    added = store.add_scratchpad(project)

    assert (added.slug, added.name, added.status, added.scratchpad) == (
        "scratchpad",
        "Scratchpad",
        "Explore",
        True,
    )
    assert store.add_scratchpad(project) is None
    assert bars(store, project) == ([], ["scratchpad", "a"])
    add(store, project, "B")
    store.start_task(project, "b")
    assert bars(store, project) == ([], ["scratchpad", "a", "b"])


def test_a_scratchpad_takes_the_project_repository_and_free_names(store):
    slug = store.create_project(ProjectMeta(name="Web", github_url="git@github.com:o/web.git")).slug
    add(store, slug, "Scratchpad")

    added = store.add_scratchpad(slug)

    assert (added.slug, added.name, added.repo) == ("scratchpad-2", "Scratchpad 2", "web")
    assert store.project(slug).focused_task == "scratchpad-2"


def test_the_scratchpad_cannot_be_deleted_or_moved_from_the_top(store, project):
    store.add_scratchpad(project)
    for name in ("a", "b"):
        add(store, project, name)
        store.start_task(project, name)

    with pytest.raises(InvalidEdit):
        store.delete_task(project, "scratchpad")
    store.reorder_tasks(project, "tasks", ["b", "a"])
    assert bars(store, project) == ([], ["scratchpad", "b", "a"])
    store.reorder_tasks(project, "tasks", ["a", "scratchpad", "b"])
    assert bars(store, project) == ([], ["scratchpad", "a", "b"])


def test_a_task_file_from_before_the_states_takes_its_state_from_its_color():
    def state(status: str, color: str) -> str:
        return TaskFile.model_validate({"name": "A", "status": status, "color": color}).state

    assert state("Backlog", "") == "backlog"
    assert state("New", "") == "new"
    assert state("Busy", "blue") == "working"
    assert state("Ask", "amber") == "needs_input"
    assert state("CI", "red") == "waiting"
    assert state("Done", "green") == "done"
    assert state("Merged", "purple") == "merged"
