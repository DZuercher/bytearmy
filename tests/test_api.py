"""Checks for the HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bytearmy.api import create_app
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
