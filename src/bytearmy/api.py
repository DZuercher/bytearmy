"""The HTTP API of the core. The UI is its first client; the CLI will be the second."""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bytearmy.agents import ORCHESTRATOR
from bytearmy.answers import render as render_answer
from bytearmy.clones import AlreadyCloning, CloneFailed, Cloner
from bytearmy.config import ACCOUNTS, load_config
from bytearmy.inbox import Inbox, MessageNotFound
from bytearmy.logpage import render
from bytearmy.models import (
    Agent,
    AgentSpawn,
    AnswerDecision,
    AnswerDraft,
    AnswerSent,
    AppState,
    Focus,
    Highlight,
    InboxState,
    Layout,
    LogEntry,
    Opened,
    OrchestratorState,
    Page,
    PageVersion,
    PaneCall,
    PaneMove,
    PaneOpen,
    Project,
    ProjectEdit,
    ProjectMeta,
    ProjectOrder,
    ProjectSummary,
    PullRequestAnswer,
    PullRequestCreate,
    PullRequestMerge,
    ReadChange,
    ReviewRequest,
    ReviewState,
    Task,
    TaskChange,
    TaskCreate,
    TaskEdit,
    TaskOrder,
    TaskStart,
    TaskSummary,
    Tool,
    UiChange,
    Unfold,
    Visibility,
    WorkflowAnswer,
    WorkflowDispatch,
    Workspace,
)
from bytearmy.orchestrator import Orchestrator
from bytearmy.prs import GitHubFailed
from bytearmy.ptys import Pty
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
from bytearmy.workflows import DEFAULT_RUN_LIMIT
from bytearmy.workspaces import (
    SPAWNING_TOOL,
    CommandNotAllowed,
    DraftDecided,
    Ended,
    InvalidLayout,
    InvalidValues,
    Key,
    NoAgentTool,
    NoDiffTool,
    NoMaster,
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


def calling_orchestrator(request: Request) -> None:
    """Only the orchestrator's program may call; its key is the token it sends."""
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not request.app.state.orchestrator.caller_ok(supplied):
        raise HTTPException(status_code=401, detail="only the orchestrator may call this")


def not_allowed(command: str, key: str) -> str:
    return (
        f"`bytearmy {command.replace('.', ' ')}` is not allowed to you: `allow` in [{key}]"
        " of config.toml lacks it, or an `allow_*` switch there is off. Ask the user."
    )


def orchestrator_may(*commands: str):
    """A dependency: config.toml must let the orchestrator run one of these commands."""

    def check(request: Request) -> None:
        orchestrator = request.app.state.orchestrator
        if not any(orchestrator.allows(command) for command in commands):
            raise HTTPException(status_code=403, detail=not_allowed(commands[0], "orchestrator"))

    return Depends(check)


def agent_may(command: str):
    """A dependency: config.toml must let the calling agent run this command."""

    def check(request: Request, caller: Caller) -> None:
        request.app.state.workspaces.check_allowed(caller, command)

    return Depends(check)


async def pump(
    socket: WebSocket,
    pty: Pty,
    write: Callable[[bytes], None],
    resized: Callable[[int, int], None],
) -> None:
    """Carry a terminal over an accepted socket: output as binary frames, input and
    resizes as JSON, and a JSON `exit` once the program has ended."""
    ended = json.dumps({"type": "exit"})
    output = pty.listen()

    async def send_output() -> None:
        while (chunk := await output.get()) is not None:
            await socket.send_bytes(chunk)
        await socket.send_text(ended)

    async def receive_input() -> None:
        while True:
            message = json.loads(await socket.receive_text())
            if message["type"] == "input":
                await asyncio.to_thread(write, message["data"].encode())
            elif message["type"] == "resize":
                await asyncio.to_thread(resized, int(message["rows"]), int(message["cols"]))

    directions = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
    try:
        await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for direction in directions:
            direction.cancel()
        await asyncio.gather(*directions, return_exceptions=True)
        pty.stop_listening(output)


def create_app(
    store: Store,
    token: str,
    ui_dist: Path | None = None,
    cloner: Cloner | None = None,
    workspaces: Workspaces | None = None,
    url: str = "",
    inbox: Inbox | None = None,
    orchestrator: Orchestrator | None = None,
) -> FastAPI:
    """The core's app. `url` is where it is served: the programs in the panes call it there.

    Without an `inbox`, the inbox asks no source: only the app the user starts asks GitHub.
    Without an `orchestrator`, it starts only once the window shows it.
    """
    cloner = cloner or Cloner(store)
    inbox = inbox or Inbox(store)
    workspaces = workspaces or Workspaces(store)
    orchestrator = orchestrator or Orchestrator(store, workspaces)
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
        # The command dialogue opens tools in the focused task of the focused project: its
        # tools are offered, with the task's pull request as the default of {task_page}.
        focused = next((p for p in whole.projects if p.slug == whole.focused_project), None)
        task = focused.focused_task if focused else None
        started = {started.slug for started in focused.tasks} if focused else set()
        whole.tools = workspaces.tools(
            focused.slug if focused else None, task if task in started else None
        )
        whole.theme = load_theme(store.root)
        # What is wrong with the config files that hold where the user looks.
        project_dir = Path(focused.path) if focused else None
        task_dir = None
        if focused and task in started:
            task_dir = Path(next(t.path for t in focused.tasks if t.slug == task))
        whole.config_errors = load_config(store.root, project_dir, task_dir)[1] + (
            ACCOUNTS.problems()
        )
        whole.inbox = inbox.state()
        whole.orchestrator = orchestrator.state()
        return whole

    # What the window and the orchestrator both do; the orchestrator gets no state back.

    def add_scratchpad(project: str) -> None:
        """Give the project its scratchpad, with a clone of its repository, if it has none."""
        scratchpad = store.add_scratchpad(project)
        if scratchpad and scratchpad.repo:
            cloner.start(project, scratchpad, store.project(project).github_url)

    def new_project(meta: ProjectMeta) -> Project:
        project = store.create_project(meta)
        add_scratchpad(project.slug)
        return store.project(project.slug)

    def drop_project(project: str) -> None:
        cloner.cancel(project)
        workspaces.stop(project)
        store.delete_project(project)

    def check_branches(new: TaskCreate) -> None:
        for name in filter(None, [new.branch, new.base]):
            if not cloner.valid_branch(name):
                raise HTTPException(status_code=400, detail=f'"{name}" is not a valid branch name.')

    def new_task(project: str, new: TaskCreate) -> Task:
        check_branches(new)
        task = store.create_task(project, new)
        if task.repo:
            cloner.start(project, task, store.project(project).github_url)
        return task

    def drop_task(project: str, task: str) -> None:
        if store.task(project, task).scratchpad:
            raise InvalidEdit("the scratchpad of a project cannot be deleted")
        cloner.cancel(project, task)
        workspaces.stop(project, task)
        store.delete_task(project, task)

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/state")
    def get_state() -> AppState:
        return state()

    @api.patch("/ui")
    def update_ui(change: UiChange) -> AppState:
        store.update_ui(change)
        return state()

    @api.post("/inbox/refresh")
    def refresh_inbox() -> AppState:
        inbox.refresh()
        return state()

    @api.post("/inbox/{message}/read")
    def mark_message(message: str, change: ReadChange) -> AppState:
        inbox.mark(message, change.unread)
        return state()

    @api.post("/inbox/{message}/dismiss")
    def dismiss_message(message: str) -> AppState:
        inbox.dismiss(message)
        return state()

    @api.put("/focus")
    def focus_project(body: Focus) -> AppState:
        store.focus_project(body.slug)
        return state()

    @api.post("/projects", status_code=201)
    def create_project(meta: ProjectMeta) -> AppState:
        new_project(meta)
        return state()

    @api.get("/projects/{project}/branches")
    def list_branches(project: str) -> list[str]:
        """The branches a new task can start from, the default branch first."""
        return cloner.branches(project)

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
        drop_project(project)
        return state()

    @api.post("/projects/{project}/tasks", status_code=201)
    def create_task(project: str, new: TaskCreate, start: bool = False) -> AppState:
        """Create a task in the backlog. With `start`, start it and its master agent at once,
        with the description as the agent's first message; with a clone, after the clone."""
        check_branches(new)
        if start:
            waiting = [
                other.name
                for other in store.project(project).backlog
                if other.slug in new.dependencies
            ]
            if waiting:
                raise Blocked(new.name, waiting)
        task = store.create_task(project, new)
        then = None
        if start:
            store.start_task(project, task.slug)
            store.focus_task(project, task.slug)

            def then() -> None:
                workspaces.start_master(project, task.slug)

        if task.repo:
            cloner.start(project, task, store.project(project).github_url, then)
        elif then is not None:
            then()
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
        """Change a task. A branch given to a task without one is checked out in its clone;
        a clone that failed gets it when it is tried again."""
        found = store.task(project, task)
        if edit.branch and not found.branch:
            if not cloner.valid_branch(edit.branch):
                raise HTTPException(
                    status_code=400, detail=f'"{edit.branch}" is not a valid branch name.'
                )
            if found.clone == "cloning":
                raise HTTPException(
                    status_code=409,
                    detail=f'"{found.name}" is still being cloned; give it a branch afterwards.',
                )
            if found.clone == "ready":
                try:
                    cloner.switch(project, found, edit.branch)
                except CloneFailed as failure:
                    raise HTTPException(
                        status_code=400,
                        detail=f'The clone could not switch to "{edit.branch}": {failure}',
                    ) from failure
        store.edit_task(project, task, edit)
        return state()

    @api.delete("/projects/{project}/tasks/{task}")
    def delete_task(project: str, task: str) -> AppState:
        drop_task(project, task)
        return state()

    @api.post(PANES, status_code=201)
    def open_pane(project: str, task: str, request: PaneOpen) -> AppState:
        workspaces.open_pane(project, task, request)
        return state()

    @api.delete(PANES + "/{pane}")
    def close_pane(project: str, task: str, pane: str) -> AppState:
        workspaces.close_pane(project, task, pane)
        return state()

    @api.post(PANES + "/{pane}/finish")
    def finish_review(project: str, task: str, pane: str) -> AppState:
        """Send the review of a diff pane and close the pane."""
        workspaces.finish_review(project, task, pane)
        return state()

    @api.get(PANES + "/{pane}/page")
    def pane_page(project: str, task: str, pane: str, request: Request) -> Page:
        """What a web pane shows. Answers only once the tool's service runs."""
        base = str(request.base_url).rstrip("/")
        files = f"{base}/files/{files_key}/{project}/{task}"
        log = f"{base}/log/{files_key}/{project}/{task}"
        answers = f"{base}/answers/{files_key}/{project}/{task}"
        return Page(url=workspaces.page(project, task, pane, files, log, answers))

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

    @api.get("/orchestrator")
    def orchestrator_state() -> OrchestratorState:
        return orchestrator.state()

    @api.post("/orchestrator/restart")
    def restart_orchestrator() -> AppState:
        orchestrator.restart()
        return state()

    # -- the `bytearmy` command of the orchestrator --------------------------
    # Its program has a key of its own and reaches every project, task and the inbox.

    above = APIRouter(prefix="/orchestrator", dependencies=[Depends(calling_orchestrator)])

    def summary(project: str, task: Task) -> TaskSummary:
        master = task.workspace.panes.get(task.workspace.master or "")
        return TaskSummary(
            slug=task.slug,
            name=task.name,
            status=task.status,
            state=task.state,
            branch=task.branch,
            description=task.description,
            path=task.path,
            master=master.agent if master else "",
            master_running=bool(master) and workspaces.master_running(project, task.slug),
        )

    def project_summary(project: Project) -> ProjectSummary:
        return ProjectSummary(
            slug=project.slug,
            name=project.name,
            description=project.description,
            github_url=project.github_url,
            path=project.path,
            backlog=[summary(project.slug, task) for task in project.backlog],
            tasks=[summary(project.slug, task) for task in project.tasks],
        )

    @above.get("/projects", dependencies=[orchestrator_may("project.list", "tasks.list")])
    def above_projects() -> list[ProjectSummary]:
        return [project_summary(project) for project in store.app_state().projects]

    @above.post("/projects", status_code=201, dependencies=[orchestrator_may("project.create")])
    def above_create_project(meta: ProjectMeta) -> ProjectSummary:
        return project_summary(new_project(meta))

    @above.delete("/projects/{project}", dependencies=[orchestrator_may("project.delete")])
    def above_delete_project(project: str) -> None:
        drop_project(project)

    @above.post(
        "/projects/{project}/tasks",
        status_code=201,
        dependencies=[orchestrator_may("tasks.create")],
    )
    def above_create_task(project: str, new: TaskCreate) -> TaskSummary:
        return summary(project, new_task(project, new))

    @above.post(
        "/projects/{project}/tasks/{task}/start", dependencies=[orchestrator_may("tasks.start")]
    )
    def above_start_task(project: str, task: str, start: TaskStart) -> TaskSummary:
        """Move the task into the task bar; with a prompt, start its master with it."""
        found = store.task(project, task)
        if start.prompt and found.clone == "cloning":
            # Its master would start outside the clone.
            raise HTTPException(
                status_code=409,
                detail=f'"{found.name}" is still being cloned; start it once the clone is ready.',
            )
        store.start_task(project, task)
        if start.prompt and start.prompt.strip():
            workspaces.start_master(project, task, start.prompt, spawned_by=ORCHESTRATOR)
        return summary(project, store.task(project, task))

    @above.delete(
        "/projects/{project}/tasks/{task}", dependencies=[orchestrator_may("tasks.delete")]
    )
    def above_delete_task(project: str, task: str) -> None:
        drop_task(project, task)

    @above.get("/inbox", dependencies=[orchestrator_may("inbox.list")])
    def above_inbox() -> InboxState:
        return inbox.state()

    @above.post("/inbox/{message}/read", dependencies=[orchestrator_may("inbox.read")])
    def above_mark_message(message: str, change: ReadChange) -> None:
        inbox.mark(message, change.unread)

    @above.post("/inbox/{message}/dismiss", dependencies=[orchestrator_may("inbox.dismiss")])
    def above_dismiss_message(message: str) -> None:
        inbox.dismiss(message)

    # -- the `bytearmy` command in the panes -------------------------------
    # Each pane's program has a key of its own and reaches only its own task.

    agent = APIRouter(prefix="/agent")

    @agent.get("/panes", dependencies=[agent_may("pane.list")])
    def agent_panes(caller: Caller) -> Workspace:
        return store.task(*caller[:2]).workspace

    @agent.post("/panes", status_code=201)
    def agent_open_pane(caller: Caller, call: PaneCall) -> Opened:
        opened = workspaces.open_for(caller, call)
        if isinstance(opened, LogEntry):
            return Opened(pane="", logged=True)
        if isinstance(opened, Ended):
            return Opened(pane=opened.pane, exit_code=opened.code, output=opened.output)
        return Opened(pane=opened.id if opened else "")

    @agent.delete("/panes/{pane}", dependencies=[agent_may("pane.close")])
    def agent_close_pane(caller: Caller, pane: str) -> None:
        workspaces.close_pane(*caller[:2], pane)

    @agent.put("/maximized", dependencies=[agent_may("pane.maximize")])
    def agent_maximize(caller: Caller, body: Focus) -> None:
        workspaces.maximize(*caller[:2], body.slug)

    @agent.put("/panes/{pane}/highlight", dependencies=[agent_may("pane.highlight")])
    def agent_highlight(caller: Caller, pane: str, body: Highlight) -> None:
        workspaces.highlight(*caller[:2], pane, body.on, beside=caller[2])

    @agent.put("/panes/{pane}/hidden", dependencies=[agent_may("pane.hide")])
    def agent_hide(caller: Caller, pane: str, body: Visibility) -> None:
        workspaces.set_hidden(*caller[:2], pane, body.hidden, body.beside or caller[2])

    @agent.put("/panes/{pane}/move", dependencies=[agent_may("pane.move")])
    def agent_move(caller: Caller, pane: str, body: PaneMove) -> None:
        workspaces.move(*caller[:2], pane, body.beside)

    @agent.get("/task", dependencies=[agent_may("task.show")])
    def agent_task(caller: Caller) -> Task:
        return store.task(*caller[:2])

    @agent.patch("/task")
    def agent_change_task(caller: Caller, change: TaskChange) -> Task:
        for field in sorted(change.model_fields_set):
            # A state has a command of its own: `bytearmy task working`, `task needs-input`, ...
            name = change.state.replace("_", "-") if field == "state" else f"set-{field}"
            workspaces.check_allowed(caller, f"task.{name}")
        store.change_task(*caller[:2], change)
        return store.task(*caller[:2])

    @agent.get("/agents", dependencies=[agent_may("agent.list")])
    def agent_list(caller: Caller) -> list[Agent]:
        return workspaces.agents(*caller[:2])

    @agent.post("/agents", status_code=201, dependencies=[agent_may("agent.spawn")])
    def agent_spawn(caller: Caller, spawn: AgentSpawn) -> Opened:
        opened = workspaces.spawn(caller, spawn)
        return Opened(pane=opened.id, agent=opened.agent)

    @agent.post("/reviews", status_code=201, dependencies=[agent_may("review")])
    def agent_request_review(caller: Caller, request: ReviewRequest) -> Opened:
        return Opened(pane=workspaces.request_review(caller, request).id)

    @agent.post("/pulls", status_code=201, dependencies=[agent_may("pr.create")])
    def agent_create_pull_request(caller: Caller, request: PullRequestCreate) -> PullRequestAnswer:
        return PullRequestAnswer(text=workspaces.create_pull_request(caller, request))

    @agent.get("/pulls", dependencies=[agent_may("pr.view")])
    def agent_view_pull_request(caller: Caller) -> PullRequestAnswer:
        return PullRequestAnswer(text=workspaces.view_pull_request(caller))

    @agent.post("/pulls/merge", dependencies=[agent_may("pr.merge")])
    def agent_merge_pull_request(caller: Caller, request: PullRequestMerge) -> PullRequestAnswer:
        return PullRequestAnswer(text=workspaces.merge_pull_request(caller, request))

    @agent.get("/workflows", dependencies=[agent_may("workflow.list")])
    def agent_list_workflows(caller: Caller) -> WorkflowAnswer:
        return WorkflowAnswer(text=workspaces.list_workflows(caller))

    @agent.post("/workflows/run", dependencies=[agent_may("workflow.run")])
    def agent_run_workflow(caller: Caller, request: WorkflowDispatch) -> WorkflowAnswer:
        return WorkflowAnswer(text=workspaces.run_workflow(caller, request))

    @agent.get("/workflows/runs", dependencies=[agent_may("workflow.runs")])
    def agent_workflow_runs(
        caller: Caller,
        workflow: str = "",
        branch: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_RUN_LIMIT,
    ) -> WorkflowAnswer:
        return WorkflowAnswer(text=workspaces.workflow_runs(caller, workflow, branch, limit))

    @agent.post("/answers", status_code=201, dependencies=[agent_may("answer.draft")])
    def agent_draft_answer(caller: Caller, request: AnswerDraft) -> Opened:
        return Opened(pane=workspaces.draft_answer(caller, request).id)

    @agent.post("/answers/{draft}/send", dependencies=[agent_may("answer.send")])
    def agent_send_answer(caller: Caller, draft: str) -> AnswerSent:
        return AnswerSent(text=workspaces.send_answer(caller, draft))

    @agent.get("/reviews/{pane}")
    def agent_review_state(caller: Caller, pane: str) -> ReviewState:
        return workspaces.review_state(caller, pane)

    @agent.get("/tools", dependencies=[agent_may("tool.list")])
    def agent_tools(caller: Caller) -> list[Tool]:
        """The tools an agent may open, as its project has them."""
        return [tool for tool in workspaces.tools(*caller[:2]) if "agent" in tool.callers]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Projects made before scratchpads existed get theirs now.
        for project in store.app_state().projects:
            add_scratchpad(project.slug)
        workspaces.start_all()
        orchestrator.start()
        inbox.start()
        yield
        inbox.shutdown()
        orchestrator.shutdown()
        cloner.shutdown()
        workspaces.shutdown()

    # FastAPI's schema and docs pages would answer without the token.
    app = FastAPI(title="bytearmy", openapi_url=None, lifespan=lifespan)
    app.state.workspaces = workspaces  # for `calling_pane`
    app.state.orchestrator = orchestrator  # for `calling_orchestrator`
    app.include_router(api)
    app.include_router(above)
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
        if pty is None:
            await socket.send_text(json.dumps({"type": "exit"}))
            await socket.close()
            return

        def resized(rows: int, cols: int) -> None:
            pty.resize(rows, cols)
            workspaces.resized(project, task, pane, rows, cols)

        await pump(socket, pty, pty.write, resized)

    @app.websocket("/api/orchestrator/terminal")
    async def orchestrator_terminal(socket: WebSocket) -> None:
        """The orchestrator's terminal, like that of a pane."""
        query = socket.query_params
        if not token_ok(query.get("token", "")):
            await socket.close(code=4401)
            return
        try:
            rows, cols = int(query.get("rows", 24)), int(query.get("cols", 80))
        except ValueError:
            await socket.close(code=4404)
            return
        pty = await asyncio.to_thread(orchestrator.attach, rows, cols)
        await socket.accept()
        if pty is None:
            await socket.send_text(json.dumps({"type": "exit"}))
            await socket.close()
            return
        await pump(socket, pty, orchestrator.write, orchestrator.resized)

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

    def check_key(key: str) -> None:
        if not secrets.compare_digest(key.encode(), files_key.encode()):
            raise HTTPException(status_code=404)

    @app.get("/answers/{key}/{project}/{task}/{pane}")
    def answer_page(key: str, project: str, task: str, pane: str) -> HTMLResponse:
        """The page of an answer pane: the conversation and the draft, to edit and decide."""
        check_key(key)
        draft = workspaces.draft(project, task, pane)
        return HTMLResponse(render_answer(draft, load_theme(store.root)))

    @app.post("/answers/{key}/{project}/{task}/{pane}/approve")
    def approve_answer(
        key: str, project: str, task: str, pane: str, decision: AnswerDecision
    ) -> None:
        """The user approves the draft, as they edited it; the agent is told."""
        check_key(key)
        workspaces.decide_answer(project, task, pane, approve=True, answer=decision.answer)

    @app.post("/answers/{key}/{project}/{task}/{pane}/discard")
    def discard_answer(key: str, project: str, task: str, pane: str) -> None:
        """The user discards the draft and answers on their own; the agent is told."""
        check_key(key)
        workspaces.decide_answer(project, task, pane, approve=False)

    def answer(status_code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(ProjectNotFound)
    def project_not_found(_request: Request, error: ProjectNotFound) -> JSONResponse:
        return answer(404, f"no project {error.args[0]!r}")

    @app.exception_handler(TaskNotFound)
    def task_not_found(_request: Request, error: TaskNotFound) -> JSONResponse:
        return answer(404, f"no task {error.args[0]!r}")

    @app.exception_handler(MessageNotFound)
    def message_not_found(_request: Request, error: MessageNotFound) -> JSONResponse:
        return answer(404, f"no message {error.args[0]!r}")

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

    @app.exception_handler(NoDiffTool)
    def no_diff_tool(_request: Request, _error: NoDiffTool) -> JSONResponse:
        return answer(404, 'no tool shows the changes: a tool in .tools needs `kind = "diff"`')

    @app.exception_handler(NoMaster)
    def no_master(_request: Request, _error: NoMaster) -> JSONResponse:
        return answer(409, "The task has no running master agent to send the review to.")

    @app.exception_handler(DraftDecided)
    def draft_decided(_request: Request, error: DraftDecided) -> JSONResponse:
        return answer(409, str(error))

    @app.exception_handler(CommandNotAllowed)
    def command_not_allowed(_request: Request, error: CommandNotAllowed) -> JSONResponse:
        return answer(403, not_allowed(*error.args))

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

    @app.exception_handler(GitHubFailed)
    def github_failed(_request: Request, error: GitHubFailed) -> JSONResponse:
        return answer(409, str(error))

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
