"""Checks for the file-system store."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bytearmy.models import ProjectMeta, UiChange, UiState
from bytearmy.store import DuplicateName, InvalidOrder, ProjectNotFound, Store, slugify


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


def create(store: Store, name: str, **fields):
    return store.create_project(ProjectMeta(name=name, **fields))


def slugs(store: Store) -> list[str]:
    return [project.slug for project in store.app_state().projects]


def plant_project(directory: Path) -> None:
    """A project directory that appeared without going through the store."""
    (directory / ".bytearmy").mkdir(parents=True)
    (directory / ".bytearmy" / "project.json").write_text('{"name": "planted"}')


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Project 1", "project-1"),
        ("  Hello,  World! ", "hello-world"),
        ("Müller & Söhne", "muller-sohne"),
        ("!!!", "project"),
        ("a" * 70 + " tail", "a" * 64),
        ("a" * 63 + " b", "a" * 63),
        ("!" + "a" * 64, "a" * 64),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_create_writes_metadata_into_a_slug_directory(store):
    project = create(store, "Project 1", description="first", github_url="git@github.com:me/p1.git")

    assert project.slug == "project-1"
    assert Path(project.path) == store.root / "project-1"
    metadata = store.root / "project-1" / ".bytearmy" / "project.json"
    assert json.loads(metadata.read_text()) == {
        "name": "Project 1",
        "description": "first",
        "github_url": "git@github.com:me/p1.git",
    }


def test_created_project_is_listed_last_and_focused(store):
    create(store, "B")
    create(store, "A")

    state = store.app_state()
    assert [project.slug for project in state.projects] == ["b", "a"]
    assert state.focused_project == "a"


@pytest.mark.parametrize("second", ["Demo", "  Demo "])
def test_a_second_project_with_the_same_name_is_refused(store, second):
    create(store, "Demo")
    before = sorted(entry.name for entry in store.root.iterdir())

    with pytest.raises(DuplicateName):
        create(store, second)

    assert sorted(entry.name for entry in store.root.iterdir()) == before
    assert slugs(store) == ["demo"]


def test_different_names_with_the_same_slug_get_distinct_directories(store):
    created = [create(store, name).slug for name in ("Demo", "demo", "DEMO")]

    assert created == ["demo", "demo-2", "demo-3"]


def test_slug_avoids_a_directory_that_differs_only_by_case(store):
    (store.root / "Demo").mkdir()

    assert create(store, "demo").slug == "demo-2"


def test_order_drops_vanished_projects_and_appends_unknown_ones_sorted(store):
    for name in ("c", "a", "b"):
        create(store, name)
    store.reorder_projects(["b", "c", "a"])
    shutil.rmtree(store.root / "c")
    plant_project(store.root / "zeta")
    plant_project(store.root / "alpha")

    assert slugs(store) == ["b", "a", "alpha", "zeta"]


def test_directories_without_readable_metadata_are_not_projects(store):
    (store.root / "plain").mkdir()
    (store.root / "broken" / ".bytearmy").mkdir(parents=True)
    (store.root / "broken" / ".bytearmy" / "project.json").write_text("not json")

    assert slugs(store) == []


@pytest.mark.parametrize("order", [["a"], ["a", "b", "ghost"], ["a", "a", "b"]])
def test_reorder_rejects_anything_but_a_permutation(store, order):
    create(store, "a")
    create(store, "b")

    with pytest.raises(InvalidOrder):
        store.reorder_projects(order)
    assert slugs(store) == ["a", "b"]


def test_delete_removes_the_directory_and_drops_order_and_focus(store):
    create(store, "a")
    create(store, "b")
    (store.root / "b" / "task").mkdir()
    (store.root / "b" / "task" / "notes.md").write_text("work")

    store.delete_project("b")

    assert not (store.root / "b").exists()
    state = store.app_state()
    assert [project.slug for project in state.projects] == ["a"]
    assert state.focused_project is None


@pytest.mark.parametrize("slug", ["../victim", "link", "plain"])
def test_delete_refuses_what_is_not_a_project_of_this_root(store, tmp_path, slug):
    victim = tmp_path / "victim"  # a real project directory, but outside the root
    plant_project(victim)
    (store.root / "link").symlink_to(victim, target_is_directory=True)
    (store.root / "plain").mkdir()
    (store.root / "plain" / "keep.txt").write_text("data")

    with pytest.raises(ProjectNotFound):
        store.delete_project(slug)

    assert (victim / ".bytearmy" / "project.json").exists()
    assert (store.root / "plain" / "keep.txt").exists()


def test_focus_requires_an_existing_project(store):
    create(store, "a")

    with pytest.raises(ProjectNotFound):
        store.focus_project("ghost")
    assert store.app_state().focused_project == "a"

    store.focus_project(None)
    assert store.app_state().focused_project is None


def test_state_survives_a_restart(store):
    for name in ("a", "b", "c"):
        create(store, name)
    store.reorder_projects(["c", "a", "b"])
    store.focus_project("a")
    store.update_ui(UiChange(left_open=False))
    store.update_ui(UiChange(bottom_open=False))

    reopened = Store(store.root).app_state()

    assert [project.slug for project in reopened.projects] == ["c", "a", "b"]
    assert reopened.focused_project == "a"
    assert reopened.ui == UiState(left_open=False, right_open=True, bottom_open=False)
