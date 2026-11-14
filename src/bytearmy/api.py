"""The HTTP API of the core. The UI is its first client; the CLI will be the second."""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from bytearmy.clones import Cloner
from bytearmy.models import (
    AppState,
    Focus,
    Layout,
    PaneOpen,
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
from bytearmy.workspaces import (
    InvalidLayout,
    InvalidValues,
    NotStarted,
    PaneNotFound,
    UnknownTool,
    Workspaces,
)

PANES = "/projects/{project}/tasks/{task}/panes"


def create_app(
    store: Store,
    token: str,
    ui_dist: Path | None = None,
    cloner: Cloner | None = None,
    workspaces: Workspaces | None = None,
) -> FastAPI:
    cloner = cloner or Cloner(store)
    workspaces = workspaces or Workspaces(store)

    def token_ok(supplied: str) -> bool:
        return secrets.compare_digest(supplied.encode(), token.encode())

    def require_token(request: Request) -> None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token_ok(supplied):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    def state() -> AppState:
        """Everything the UI shows. Every change answers with it, so the UI never merges."""
        whole = store.app_state()
        whole.tools = workspaces.tools()
        return whole

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/state")
    def get_state() -> AppState:
        return state()

    @api.patch("/ui")
    def update_ui(change: UiChange) -> AppState:
        store.update_ui(change)
        return state()

    @api.put("/focus")
    def focus_project(body: Focus) -> AppState:
        store.focus_project(body.slug)
        return state()

    @api.post("/projects", status_code=201)
    def create_project(meta: ProjectMeta) -> AppState:
        store.create_project(meta)
        return state()

    @api.put("/projects/order")
    def reorder_projects(body: ProjectOrder) -> AppState:
        store.reorder_projects(body.order)
        return state()

    @api.delete("/projects/{project}")
    def delete_project(project: str) -> AppState:
        cloner.cancel(project)
        workspaces.stop(project)
        store.delete_project(project)
        return state()

    @api.post("/projects/{project}/tasks", status_code=201)
    def create_task(project: str, new: TaskCreate) -> AppState:
        if new.branch and not cloner.valid_branch(new.branch):
            raise HTTPException(
                status_code=400, detail=f'"{new.branch}" is not a valid branch name.'
            )
        task = store.create_task(project, new)
        if task.repo:
            cloner.start(project, task, store.project(project).github_url)
        return state()

    @api.put("/projects/{project}/tasks/order")
    def reorder_tasks(project: str, body: TaskOrder) -> AppState:
        store.reorder_tasks(project, body.bar, body.order)
        return state()

    @api.put("/projects/{project}/tasks/focus")
    def focus_task(project: str, body: Focus) -> AppState:
        store.focus_task(project, body.slug)
        return state()

    @api.post("/projects/{project}/tasks/{task}/start")
    def start_task(project: str, task: str) -> AppState:
        store.start_task(project, task)
        return state()

    @api.delete("/projects/{project}/tasks/{task}")
    def delete_task(project: str, task: str) -> AppState:
        cloner.cancel(project, task)
        workspaces.stop(project, task)
        store.delete_task(project, task)
        return state()

    @api.post(PANES, status_code=201)
    def open_pane(project: str, task: str, request: PaneOpen) -> AppState:
        workspaces.open_pane(project, task, request)
        return state()

    @api.delete(PANES + "/{pane}")
    def close_pane(project: str, task: str, pane: str) -> AppState:
        workspaces.close_pane(project, task, pane)
        return state()

    @api.put("/projects/{project}/tasks/{task}/layout")
    def set_layout(project: str, task: str, layout: Layout) -> AppState:
        workspaces.set_tree(project, task, layout.tree)
        return state()

    @api.put("/projects/{project}/tasks/{task}/maximized")
    def maximize_pane(project: str, task: str, body: Focus) -> AppState:
        workspaces.maximize(project, task, body.slug)
        return state()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        workspaces.start_all()
        yield
        cloner.shutdown()
        workspaces.shutdown()

    # FastAPI's schema and docs pages would answer without the token.
    app = FastAPI(title="bytearmy", openapi_url=None, lifespan=lifespan)
    app.include_router(api)

    @app.websocket("/api" + PANES + "/{pane}/terminal")
    async def terminal(socket: WebSocket, project: str, task: str, pane: str) -> None:
        """The terminal of a pane: output as binary frames, input and resizes as JSON."""
        query = socket.query_params
        if not token_ok(query.get("token", "")):
            await socket.close(code=4401)
            return
        try:
            rows, cols = int(query.get("rows", 24)), int(query.get("cols", 80))
            pty = await asyncio.to_thread(workspaces.attach, project, task, pane, rows, cols)
        except (ProjectNotFound, TaskNotFound, PaneNotFound, ValueError):
            await socket.close(code=4404)
            return
        await socket.accept()
        ended = json.dumps({"type": "exit"})
        if pty is None:
            await socket.send_text(ended)
            await socket.close()
            return
        output = pty.listen()

        async def send_output() -> None:
            while (chunk := await output.get()) is not None:
                await socket.send_bytes(chunk)
            await socket.send_text(ended)

        async def receive_input() -> None:
            while True:
                message = json.loads(await socket.receive_text())
                if message["type"] == "input":
                    await asyncio.to_thread(pty.write, message["data"].encode())
                elif message["type"] == "resize":
                    size = int(message["rows"]), int(message["cols"])
                    pty.resize(*size)
                    await asyncio.to_thread(workspaces.resized, project, task, pane, *size)

        directions = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
        try:
            await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for direction in directions:
                direction.cancel()
            await asyncio.gather(*directions, return_exceptions=True)
            pty.stop_listening(output)

    def answer(status_code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(ProjectNotFound)
    def project_not_found(_request: Request, error: ProjectNotFound) -> JSONResponse:
        return answer(404, f"no project {error.args[0]!r}")

    @app.exception_handler(TaskNotFound)
    def task_not_found(_request: Request, error: TaskNotFound) -> JSONResponse:
        return answer(404, f"no task {error.args[0]!r}")

    @app.exception_handler(PaneNotFound)
    def pane_not_found(_request: Request, error: PaneNotFound) -> JSONResponse:
        return answer(404, f"no pane {error.args[0]!r}")

    @app.exception_handler(UnknownTool)
    def unknown_tool(_request: Request, error: UnknownTool) -> JSONResponse:
        return answer(404, f"no tool {error.args[0]!r}")

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

    @app.exception_handler(NotStarted)
    def not_started(_request: Request, error: NotStarted) -> JSONResponse:
        return answer(409, f'"{error.args[0]}" is still in the backlog.')

    @app.exception_handler(UnknownDependency)
    def unknown_dependency(_request: Request, error: UnknownDependency) -> JSONResponse:
        return answer(400, f"no task {error.args[0]!r} to depend on")

    @app.exception_handler(InvalidOrder)
    def invalid_order(_request: Request, error: InvalidOrder) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(InvalidValues)
    def invalid_values(_request: Request, error: InvalidValues) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(InvalidLayout)
    def invalid_layout(_request: Request, error: InvalidLayout) -> JSONResponse:
        return answer(400, str(error))

    if ui_dist is not None and ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))
    return app


def schema() -> dict:
    """The API schema as the UI's type generator reads it; it depends on no data."""
    with tempfile.TemporaryDirectory() as scratch:
        return create_app(Store(Path(scratch)), token="").openapi()
