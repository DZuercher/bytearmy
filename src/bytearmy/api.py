"""The HTTP API of the core. The UI is its first client; the CLI will be the second."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from bytearmy.clones import Cloner
from bytearmy.models import (
    AppState,
    Focus,
    ProjectMeta,
    ProjectOrder,
    TaskCreate,
    TaskOrder,
    UiChange,
)
from bytearmy.store import (
    Blocked,
    DuplicateName,
    InvalidOrder,
    ProjectNotFound,
    Store,
    TaskNotFound,
    UnknownDependency,
)


def create_app(
    store: Store, token: str, ui_dist: Path | None = None, cloner: Cloner | None = None
) -> FastAPI:
    cloner = cloner or Cloner(store)

    def require_token(request: Request) -> None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    # Every change answers with the complete new state, so the UI never has to merge.
    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/state")
    def get_state() -> AppState:
        return store.app_state()

    @api.patch("/ui")
    def update_ui(change: UiChange) -> AppState:
        store.update_ui(change)
        return store.app_state()

    @api.put("/focus")
    def focus_project(body: Focus) -> AppState:
        store.focus_project(body.slug)
        return store.app_state()

    @api.post("/projects", status_code=201)
    def create_project(meta: ProjectMeta) -> AppState:
        store.create_project(meta)
        return store.app_state()

    @api.put("/projects/order")
    def reorder_projects(body: ProjectOrder) -> AppState:
        store.reorder_projects(body.order)
        return store.app_state()

    @api.delete("/projects/{project}")
    def delete_project(project: str) -> AppState:
        cloner.cancel(project)
        store.delete_project(project)
        return store.app_state()

    @api.post("/projects/{project}/tasks", status_code=201)
    def create_task(project: str, new: TaskCreate) -> AppState:
        if new.branch and not cloner.valid_branch(new.branch):
            raise HTTPException(status_code=400, detail=f'"{new.branch}" is not a valid branch name.')
        task = store.create_task(project, new)
        if task.repo:
            cloner.start(project, task, store.project(project).github_url)
        return store.app_state()

    @api.put("/projects/{project}/tasks/order")
    def reorder_tasks(project: str, body: TaskOrder) -> AppState:
        store.reorder_tasks(project, body.bar, body.order)
        return store.app_state()

    @api.put("/projects/{project}/tasks/focus")
    def focus_task(project: str, body: Focus) -> AppState:
        store.focus_task(project, body.slug)
        return store.app_state()

    @api.post("/projects/{project}/tasks/{task}/start")
    def start_task(project: str, task: str) -> AppState:
        store.start_task(project, task)
        return store.app_state()

    @api.delete("/projects/{project}/tasks/{task}")
    def delete_task(project: str, task: str) -> AppState:
        cloner.cancel(project, task)
        store.delete_task(project, task)
        return store.app_state()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        cloner.shutdown()

    # FastAPI's schema and docs pages would answer without the token.
    app = FastAPI(title="bytearmy", openapi_url=None, lifespan=lifespan)
    app.include_router(api)

    def answer(status_code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(ProjectNotFound)
    def project_not_found(_request: Request, error: ProjectNotFound) -> JSONResponse:
        return answer(404, f"no project {error.args[0]!r}")

    @app.exception_handler(TaskNotFound)
    def task_not_found(_request: Request, error: TaskNotFound) -> JSONResponse:
        return answer(404, f"no task {error.args[0]!r}")

    @app.exception_handler(DuplicateName)
    def duplicate_name(_request: Request, error: DuplicateName) -> JSONResponse:
        kind, name = error.args
        return answer(409, f'A {kind} named "{name}" already exists.')

    @app.exception_handler(Blocked)
    def blocked(_request: Request, error: Blocked) -> JSONResponse:
        name, waiting = error.args
        others = " and ".join(f'"{other}"' for other in waiting)
        verb = "is" if len(waiting) == 1 else "are"
        return answer(409, f'"{name}" waits for {others}, which {verb} still in the backlog.')

    @app.exception_handler(UnknownDependency)
    def unknown_dependency(_request: Request, error: UnknownDependency) -> JSONResponse:
        return answer(400, f"no task {error.args[0]!r} to depend on")

    @app.exception_handler(InvalidOrder)
    def invalid_order(_request: Request, error: InvalidOrder) -> JSONResponse:
        return answer(400, str(error))

    if ui_dist is not None and ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))
    return app
