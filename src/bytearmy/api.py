"""The HTTP API of the core. The UI is its first client; the CLI will be the second."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from bytearmy.models import AppState, Focus, ProjectMeta, ProjectOrder, UiChange
from bytearmy.store import DuplicateName, InvalidOrder, ProjectNotFound, Store


def create_app(store: Store, token: str, ui_dist: Path | None = None) -> FastAPI:
    def require_token(request: Request) -> None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    # Every change answers with the complete new state, so the UI never has to merge.
    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/state")
    def get_state() -> AppState:
        return store.app_state()

    @api.post("/projects", status_code=201)
    def create_project(meta: ProjectMeta) -> AppState:
        store.create_project(meta)
        return store.app_state()

    @api.delete("/projects/{slug}")
    def delete_project(slug: str) -> AppState:
        store.delete_project(slug)
        return store.app_state()

    @api.put("/projects/order")
    def reorder_projects(body: ProjectOrder) -> AppState:
        store.reorder_projects(body.order)
        return store.app_state()

    @api.put("/focus")
    def focus_project(body: Focus) -> AppState:
        store.focus_project(body.slug)
        return store.app_state()

    @api.patch("/ui")
    def update_ui(change: UiChange) -> AppState:
        store.update_ui(change)
        return store.app_state()

    # FastAPI's schema and docs pages would answer without the token.
    app = FastAPI(title="bytearmy", openapi_url=None)
    app.include_router(api)

    @app.exception_handler(ProjectNotFound)
    def project_not_found(_request: Request, error: ProjectNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"no project {error.args[0]!r}"})

    @app.exception_handler(DuplicateName)
    def duplicate_name(_request: Request, error: DuplicateName) -> JSONResponse:
        detail = f'A project named "{error.args[0]}" already exists.'
        return JSONResponse(status_code=409, content={"detail": detail})

    @app.exception_handler(InvalidOrder)
    def invalid_order(_request: Request, error: InvalidOrder) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    if ui_dist is not None and ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))
    return app
