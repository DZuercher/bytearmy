"""The HTTP API of the core. The UI is its first client; the CLI will be the second."""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bytearmy.clones import AlreadyCloning, Cloner
from bytearmy.logpage import render
from bytearmy.models import (
    Agent,
    AgentSpawn,
    AppState,
    Focus,
    Highlight,
    Layout,
    LogEntry,
    Opened,
    Page,
    PageVersion,
    PaneCall,
    PaneMove,
    PaneOpen,
    ProjectEdit,
    ProjectMeta,
    ProjectOrder,
    Task,
    TaskChange,
    TaskCreate,
    TaskEdit,
    TaskOrder,
    Tool,
    UiChange,
    Unfold,
    Visibility,
    Workspace,
)
from bytearmy.store import (
    Blocked,
    DuplicateName,
    InvalidEdit,
    InvalidOrder,
    ProjectNotFound,
    Store,
    TaskNotFound,
    UnknownDependency,
)
from bytearmy.themes import load_theme, stamp
from bytearmy.tools import ToolFailed
from bytearmy.workspaces import (
    SPAWNING_TOOL,
    InvalidLayout,
    InvalidValues,
    Key,
    NoAgentTool,
    NotForAgents,
    NotStarted,
    PaneNotFound,
    RoleTaken,
    UnknownTool,
    Workspaces,
)

PANES = "/projects/{project}/tasks/{task}/panes"
CHANGE_CHECK_SECONDS = 0.2  # how often the change channel looks for a new version


def calling_pane(request: Request) -> Key:
    """The pane whose program calls; its key is the token it sends."""
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    caller = request.app.state.workspaces.caller(supplied)
    if caller is None:
        raise HTTPException(status_code=401, detail="not called from a running bytearmy pane")
    return caller


Caller = Annotated[Key, Depends(calling_pane)]


def create_app(
    store: Store,
    token: str,
    ui_dist: Path | None = None,
    cloner: Cloner | None = None,
    workspaces: Workspaces | None = None,
    url: str = "",
) -> FastAPI:
    """The core's app. `url` is where it is served: the programs in the panes call it there."""
    cloner = cloner or Cloner(store)
    workspaces = workspaces or Workspaces(store)
    if url:
        workspaces.url = url
    # Pages in web panes cannot send the token, and must not know it: the files and logs
    # of the tasks are served under a key of their own.
    files_key = secrets.token_urlsafe(16)

    def token_ok(supplied: str) -> bool:
        return secrets.compare_digest(supplied.encode(), token.encode())

    def require_token(request: Request) -> None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token_ok(supplied):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    def state() -> AppState:
        """Everything the UI shows. Every change answers with it, so the UI never merges."""
        whole = store.app_state()
        slugs = {project.slug for project in whole.projects}
        # The command dialogue opens tools in the focused project: its tools are offered.
        focused = whole.focused_project if whole.focused_project in slugs else None
        whole.tools = workspaces.tools(focused)
        whole.theme = load_theme(store.root)
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

    @api.put("/projects/{project}")
    def edit_project(project: str, edit: ProjectEdit) -> AppState:
        store.edit_project(project, edit)
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

    @api.post("/projects/{project}/tasks/{task}/clone")
    def clone_again(project: str, task: str) -> AppState:
        """Try a failed clone of the project's repository again."""
        found = store.task(project, task)
        if found.clone != "failed":
            raise HTTPException(
                status_code=409, detail=f'"{found.name}" has no failed clone to try again.'
            )
        cloner.start(project, found, store.project(project).github_url)
        return state()

    @api.put("/projects/{project}/tasks/{task}")
    def edit_task(project: str, task: str, edit: TaskEdit) -> AppState:
        store.edit_task(project, task, edit)
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

    @api.get(PANES + "/{pane}/page")
    def pane_page(project: str, task: str, pane: str, request: Request) -> Page:
        """What a web pane shows. Answers only once the tool's service runs."""
        base = str(request.base_url).rstrip("/")
        files = f"{base}/files/{files_key}/{project}/{task}"
        log = f"{base}/log/{files_key}/{project}/{task}"
        return Page(url=workspaces.page(project, task, pane, files, log))

    @api.get(PANES + "/{pane}/page/version")
    def pane_page_version(project: str, task: str, pane: str) -> PageVersion:
        """Asked again and again by a web pane, which loads its page anew on a change."""
        return PageVersion(version=workspaces.page_version(project, task, pane))

    @api.put("/projects/{project}/tasks/{task}/layout")
    def set_layout(project: str, task: str, layout: Layout) -> AppState:
        workspaces.set_tree(project, task, layout.tree)
        return state()

    @api.put("/projects/{project}/tasks/{task}/maximized")
    def maximize_pane(project: str, task: str, body: Focus) -> AppState:
        workspaces.maximize(project, task, body.slug)
        return state()

    @api.put("/projects/{project}/tasks/{task}/hidden-open")
    def list_hidden(project: str, task: str, body: Unfold) -> AppState:
        workspaces.list_hidden(project, task, body.open)
        return state()

    @api.put(PANES + "/{pane}/hidden")
    def hide_pane(project: str, task: str, pane: str, body: Visibility) -> AppState:
        workspaces.set_hidden(project, task, pane, body.hidden, body.beside)
        return state()

    @api.put(PANES + "/{pane}/highlight")
    def highlight_pane(project: str, task: str, pane: str, body: Highlight) -> AppState:
        workspaces.highlight(project, task, pane, body.on)
        return state()

    # -- the `bytearmy` command in the panes -------------------------------
    # Each pane's program has a key of its own and reaches only its own task.

    agent = APIRouter(prefix="/agent")

    @agent.get("/panes")
    def agent_panes(caller: Caller) -> Workspace:
        return store.task(*caller[:2]).workspace

    @agent.post("/panes", status_code=201)
    def agent_open_pane(caller: Caller, call: PaneCall) -> Opened:
        opened = workspaces.open_for(caller, call)
        if isinstance(opened, LogEntry):
            return Opened(pane="", logged=True)
        return Opened(pane=opened.id if opened else "")

    @agent.delete("/panes/{pane}")
    def agent_close_pane(caller: Caller, pane: str) -> None:
        workspaces.close_pane(*caller[:2], pane)

    @agent.put("/maximized")
    def agent_maximize(caller: Caller, body: Focus) -> None:
        workspaces.maximize(*caller[:2], body.slug)

    @agent.put("/panes/{pane}/highlight")
    def agent_highlight(caller: Caller, pane: str, body: Highlight) -> None:
        workspaces.highlight(*caller[:2], pane, body.on, beside=caller[2])

    @agent.put("/panes/{pane}/hidden")
    def agent_hide(caller: Caller, pane: str, body: Visibility) -> None:
        workspaces.set_hidden(*caller[:2], pane, body.hidden, body.beside or caller[2])

    @agent.put("/panes/{pane}/move")
    def agent_move(caller: Caller, pane: str, body: PaneMove) -> None:
        workspaces.move(*caller[:2], pane, body.beside)

    @agent.get("/task")
    def agent_task(caller: Caller) -> Task:
        return store.task(*caller[:2])

    @agent.patch("/task")
    def agent_change_task(caller: Caller, change: TaskChange) -> Task:
        store.change_task(*caller[:2], change)
        return store.task(*caller[:2])

    @agent.get("/agents")
    def agent_list(caller: Caller) -> list[Agent]:
        return workspaces.agents(*caller[:2])

    @agent.post("/agents", status_code=201)
    def agent_spawn(caller: Caller, spawn: AgentSpawn) -> Opened:
        opened = workspaces.spawn(caller, spawn)
        return Opened(pane=opened.id, agent=opened.agent)

    @agent.get("/tools")
    def agent_tools(caller: Caller) -> list[Tool]:
        """The tools an agent may open, as its project has them."""
        return [tool for tool in workspaces.tools(caller[0]) if "agent" in tool.callers]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        workspaces.start_all()
        yield
        cloner.shutdown()
        workspaces.shutdown()

    # FastAPI's schema and docs pages would answer without the token.
    app = FastAPI(title="bytearmy", openapi_url=None, lifespan=lifespan)
    app.state.workspaces = workspaces  # for `calling_pane`
    app.include_router(api)
    app.include_router(agent)

    @app.websocket("/api/changes")
    async def changes(socket: WebSocket) -> None:
        """Sends the store's version whenever it or the color scheme changed, so the window
        fetches the state."""
        if not token_ok(socket.query_params.get("token", "")):
            await socket.close(code=4401)
            return
        await socket.accept()

        async def send_versions() -> None:
            sent = None
            while True:
                # The color scheme is a file the user edits: it changes outside the store.
                current = (store.version, await asyncio.to_thread(stamp, store.root))
                if current != sent:
                    sent = current
                    await socket.send_text(str(store.version))
                await asyncio.sleep(CHANGE_CHECK_SECONDS)

        async def until_closed() -> None:
            while (await socket.receive())["type"] != "websocket.disconnect":
                pass

        directions = [asyncio.create_task(send_versions()), asyncio.create_task(until_closed())]
        try:
            await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for direction in directions:
                direction.cancel()
            await asyncio.gather(*directions, return_exceptions=True)

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

    @app.get("/files/{key}/{project}/{task}/{path:path}")
    def task_file(key: str, project: str, task: str, path: str) -> FileResponse:
        """A file below a task directory, for the pages of web panes."""
        if not secrets.compare_digest(key.encode(), files_key.encode()):
            raise HTTPException(status_code=404)
        directory = Path(store.task(project, task).path).resolve()
        file = (directory / path).resolve()
        if not file.is_relative_to(directory) or not file.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(file)

    @app.get("/log/{key}/{project}/{task}")
    def task_log(key: str, project: str, task: str) -> HTMLResponse:
        """The log of a task as a page, for web panes."""
        if not secrets.compare_digest(key.encode(), files_key.encode()):
            raise HTTPException(status_code=404)
        entries = store.log(project, task)
        return HTMLResponse(render(store.task(project, task).name, entries, load_theme(store.root)))

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

    @app.exception_handler(NoAgentTool)
    def no_agent_tool(_request: Request, _error: NoAgentTool) -> JSONResponse:
        return answer(
            404,
            f"no tool starts agents: .tools/{SPAWNING_TOOL}.toml needs `agent = true`"
            " and a `spawn` line",
        )

    @app.exception_handler(NotForAgents)
    def not_for_agents(_request: Request, error: NotForAgents) -> JSONResponse:
        return answer(403, f"only the user may open the tool {error.args[0]!r}")

    @app.exception_handler(RoleTaken)
    def role_taken(_request: Request, error: RoleTaken) -> JSONResponse:
        return answer(409, f"this task already has an agent with the role {error.args[0]!r}")

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

    @app.exception_handler(AlreadyCloning)
    def already_cloning(_request: Request, error: AlreadyCloning) -> JSONResponse:
        return answer(409, f'"{error.args[0]}" is being cloned already.')

    @app.exception_handler(UnknownDependency)
    def unknown_dependency(_request: Request, error: UnknownDependency) -> JSONResponse:
        return answer(400, f"no task {error.args[0]!r} to depend on")

    @app.exception_handler(InvalidEdit)
    def invalid_edit(_request: Request, error: InvalidEdit) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(InvalidOrder)
    def invalid_order(_request: Request, error: InvalidOrder) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(InvalidValues)
    def invalid_values(_request: Request, error: InvalidValues) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(InvalidLayout)
    def invalid_layout(_request: Request, error: InvalidLayout) -> JSONResponse:
        return answer(400, str(error))

    @app.exception_handler(ToolFailed)
    def tool_failed(_request: Request, error: ToolFailed) -> JSONResponse:
        return answer(502, str(error))

    if ui_dist is not None and ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))
    return app


def schema() -> dict:
    """The API schema as the UI's type generator reads it; it depends on no data."""
    with tempfile.TemporaryDirectory() as scratch:
        return create_app(Store(Path(scratch)), token="").openapi()
