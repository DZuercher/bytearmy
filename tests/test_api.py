"""Checks for the HTTP API."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bytearmy.api import create_app
from bytearmy.clones import Cloner
from bytearmy.store import Store

TOKEN = "secret"


@pytest.fixture
def client(tmp_path):
    app = create_app(Store(tmp_path / "root"), token=TOKEN)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as test_client:
        yield test_client


def slugs(response) -> list[str]:
    return [project["slug"] for project in response.json()["projects"]]


@pytest.mark.parametrize("authorization", ["", "Bearer wrong"])
def test_every_route_requires_the_token(client, authorization):
    routes = [
        (method.upper(), path)
        for path, operations in client.app.openapi()["paths"].items()
        for method in operations
    ]
    assert ("GET", "/api/state") in routes

    for method, path in routes:
        response = client.request(method, path, headers={"Authorization": authorization})
        assert response.status_code == 401, (method, path)
    assert client.get("/api/state").status_code == 200


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_schema_and_docs_pages_are_not_served(client, path):
    assert client.get(path).status_code == 404


def test_project_lifecycle_over_http(client):
    created = client.post(
        "/api/projects", json={"name": "Project 1", "description": "d", "github_url": ""}
    )
    assert created.status_code == 201
    assert slugs(created) == ["project-1"]
    client.post("/api/projects", json={"name": "Second"})

    reordered = client.put("/api/projects/order", json={"order": ["second", "project-1"]})
    assert slugs(reordered) == ["second", "project-1"]

    focused = client.put("/api/focus", json={"slug": "project-1"})
    assert focused.json()["focused_project"] == "project-1"

    ui = client.patch("/api/ui", json={"left_open": False})
    assert ui.json()["ui"] == {"left_open": False, "right_open": True, "bottom_open": True}

    deleted = client.delete("/api/projects/second")
    assert slugs(deleted) == ["project-1"]
    assert client.get("/api/state").json() == deleted.json()


def test_errors_map_to_status_codes(client):
    client.post("/api/projects", json={"name": "a"})

    assert client.post("/api/projects", json={"name": "   "}).status_code == 422
    assert client.post("/api/projects", json={"name": "a"}).status_code == 409
    assert client.delete("/api/projects/ghost").status_code == 404
    assert client.put("/api/focus", json={"slug": "ghost"}).status_code == 404
    assert client.put("/api/projects/order", json={"order": []}).status_code == 400


# -- tasks ------------------------------------------------------------------


def bars(response, project: str = "demo") -> tuple[list[str], list[str]]:
    """The slugs in the backlog and in the task bar of a project."""
    found = next(p for p in response.json()["projects"] if p["slug"] == project)
    return [task["slug"] for task in found["backlog"]], [task["slug"] for task in found["tasks"]]


def task(client, slug: str, project: str = "demo") -> dict:
    found = next(p for p in client.get("/api/state").json()["projects"] if p["slug"] == project)
    return next(t for t in [*found["backlog"], *found["tasks"]] if t["slug"] == slug)


def test_task_lifecycle_over_http(client):
    client.post("/api/projects", json={"name": "Demo"})
    tasks = "/api/projects/demo/tasks"

    created = client.post(tasks, json={"name": "A", "description": "first"})
    assert created.status_code == 201
    assert bars(created) == (["a"], [])
    client.post(tasks, json={"name": "B"})
    client.post(tasks, json={"name": "C"})

    reordered = client.put(f"{tasks}/order", json={"bar": "backlog", "order": ["c", "b", "a"]})
    assert bars(reordered) == (["c", "b", "a"], [])

    started = client.post(f"{tasks}/b/start")
    assert bars(started) == (["c", "a"], ["b"])

    focused = client.put(f"{tasks}/focus", json={"slug": "b"})
    assert focused.json()["projects"][0]["focused_task"] == "b"

    deleted = client.delete(f"{tasks}/c")
    assert bars(deleted) == (["a"], ["b"])
    assert client.get("/api/state").json() == deleted.json()


def test_task_errors_map_to_status_codes(client):
    client.post("/api/projects", json={"name": "Demo"})
    tasks = "/api/projects/demo/tasks"
    client.post(tasks, json={"name": "Design API"})
    client.post(tasks, json={"name": "Fix login", "dependencies": ["design-api"]})

    assert client.post("/api/projects/ghost/tasks", json={"name": "x"}).status_code == 404
    assert client.post(tasks, json={"name": "  "}).status_code == 422
    duplicate = client.post(tasks, json={"name": "Fix login"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == 'A task named "Fix login" already exists.'
    assert client.post("/api/projects", json={"name": "Demo"}).json()["detail"] == (
        'A project named "Demo" already exists.'
    )
    assert client.post(tasks, json={"name": "x", "dependencies": ["ghost"]}).status_code == 400
    assert client.post(tasks, json={"name": "x", "branch": "bad..name"}).status_code == 400
    assert client.post(f"{tasks}/ghost/start").status_code == 404
    assert client.delete(f"{tasks}/ghost").status_code == 404
    assert client.put(f"{tasks}/order", json={"bar": "tasks", "order": ["ghost"]}).status_code == 400
    assert bars(client.get("/api/state")) == (["design-api", "fix-login"], [])

    blocked = client.post(f"{tasks}/fix-login/start")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == (
        '"Fix login" waits for "Design API", which is still in the backlog.'
    )

    client.post(tasks, json={"name": "Ship", "dependencies": ["design-api", "fix-login"]})
    assert client.post(f"{tasks}/ship/start").json()["detail"] == (
        '"Ship" waits for "Design API" and "Fix login", which are still in the backlog.'
    )


def wait_while_cloning(client, slug: str, project: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while (found := task(client, slug, project))["clone"] == "cloning":
        assert time.monotonic() < deadline, "the clone did not end"
        time.sleep(0.02)
    return found


def test_creating_a_task_clones_the_repository_of_its_project(client, remote):
    client.post("/api/projects", json={"name": "Widgets", "github_url": str(remote)})
    client.post("/api/projects", json={"name": "Plain"})

    client.post("/api/projects/widgets/tasks", json={"name": "A", "branch": "feature"})
    client.post("/api/projects/plain/tasks", json={"name": "A"})

    cloned = wait_while_cloning(client, "a", "widgets")
    assert (cloned["clone"], cloned["repo"]) == ("ready", "widgets")
    assert (Path(cloned["path"]) / "widgets" / "feature.txt").exists()
    assert task(client, "a", "plain")["clone"] == "none"


@pytest.mark.parametrize("target", ["/api/projects/widgets/tasks/a", "/api/projects/widgets"])
def test_deleting_a_task_or_its_project_stops_the_running_clone(tmp_path, remote, gated_git, target):
    store = Store(tmp_path / "root")
    app = create_app(store, token=TOKEN, cloner=Cloner(store, git=gated_git.command))
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        client.post("/api/projects", json={"name": "Widgets", "github_url": str(remote)})
        client.post("/api/projects/widgets/tasks", json={"name": "A"})
        gated_git.wait_until_running()

        assert client.delete(target).status_code == 200

        assert not gated_git.running()
        assert not (store.root / "widgets" / "a").exists()


def test_closing_the_app_stops_running_clones(tmp_path, remote, gated_git):
    store = Store(tmp_path / "root")
    app = create_app(store, token=TOKEN, cloner=Cloner(store, git=gated_git.command))
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        client.post("/api/projects", json={"name": "Widgets", "github_url": str(remote)})
        client.post("/api/projects/widgets/tasks", json={"name": "A"})
        gated_git.wait_until_running()

    assert not gated_git.running()
