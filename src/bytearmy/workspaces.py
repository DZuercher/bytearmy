"""The workspaces of the started tasks: their panes and the programs running in them."""

from __future__ import annotations

import os
import secrets
import shlex
import sys
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from bytearmy import diffs, themes
from bytearmy.agents import (
    EARLIER_INSTRUCTIONS,
    INSTRUCTIONS,
    INSTRUCTIONS_FILE,
    MASTER,
    agent_name,
    next_role,
    write_instructions,
)
from bytearmy.layout import add_leaf, leaves, remove_leaf
from bytearmy.models import (
    BACKLOG,
    Agent,
    AgentSpawn,
    Diff,
    DiffMode,
    LogEntry,
    Pane,
    PaneCall,
    PaneOpen,
    Review,
    ReviewRequest,
    ReviewState,
    Split,
    Task,
    Tool,
    Workspace,
)
from bytearmy.ptys import Pty
from bytearmy.services import Services, launch
from bytearmy.store import LOG_FILE, METADATA_DIR, ProjectNotFound, Store, TaskNotFound
from bytearmy.tools import (
    command_line,
    default_value,
    ensure_premade,
    external_command,
    load_tools,
    page_url,
    write_premade,
)

PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"  # bracketed paste, as terminals send it
SUBMIT_DELAY_SECONDS = 0.3  # Claude takes a paste as such only if Enter comes after it
REVIEWS_DIR = "reviews"  # below a task's metadata: the drafts of its reviews
CLOSED_REVIEW = "The user closed the review without finishing it."
COULD_NOT_START = 127  # the exit code a shell gives for a command it cannot find
SPAWNING_TOOL = "claude"  # starts agents for a pane that is not an agent itself

Key = tuple[str, str, str]  # project, task, pane


class UnknownTool(LookupError):
    pass


class NoAgentTool(LookupError):
    """No tool can start an agent with a prompt."""


class NotForAgents(PermissionError):
    """args: the id of a tool that only the user may open."""


class RoleTaken(ValueError):
    """args: a role that another agent of the task already has."""


class PaneNotFound(LookupError):
    pass


class NotStarted(ValueError):
    """args: the name of a task that is still in the backlog."""


class InvalidValues(ValueError):
    pass


class InvalidLayout(ValueError):
    pass


class NoDiffTool(LookupError):
    """No tool shows the changes of a task."""


class NoMaster(LookupError):
    """The task has no running master agent to send a review to."""


def pane_env(environ: Mapping[str, str], prefix: str) -> dict[str, str]:
    """The user's environment without what the core's own environment manager added."""
    env = {
        name: value for name, value in environ.items() if not name.startswith(("PIXI_", "CONDA_"))
    }
    path = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and not entry.startswith(prefix)
    ]
    env["PATH"] = os.pathsep.join(path)
    env.update(TERM="xterm-256color", COLORTERM="truecolor", TERM_PROGRAM="bytearmy")
    return env


def pane_command(bin_dir: Path) -> Path:
    """Write the `bytearmy` command of the panes: this Python, running bytearmy.cli."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / "bytearmy"
    # -I: what lies in the directory the command is called from cannot replace a module.
    command.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -m bytearmy.cli "$@"\n')
    command.chmod(0o755)
    return command


class Workspaces:
    def __init__(self, store: Store, shell: Sequence[str] | None = None, url: str = "") -> None:
        self._store = store
        # A login shell that reads the user's startup files: functions and aliases work.
        self._shell = list(shell or [os.environ.get("SHELL", "/bin/sh"), "-l", "-i"])
        self.url = url  # where the programs in the panes reach the core
        self._lock = threading.RLock()
        self._ptys: dict[Key, Pty] = {}
        self._ending: set[Key] = set()  # panes whose program is being ended on purpose
        self._keys: dict[Key, str] = {}  # the key each pane's program calls the core with
        self._reviews: dict[Key, str] = {}  # finished reviews an agent asked for, until it reads
        self._stopping = False
        self._logs = store.root / METADATA_DIR / "logs"  # of programs without a terminal
        self._services = Services(self._logs)
        self._bin = pane_command(store.root / METADATA_DIR / "bin").parent
        ensure_premade(store.root)
        write_premade(store.root / INSTRUCTIONS_FILE, INSTRUCTIONS, EARLIER_INSTRUCTIONS)
        themes.ensure_premade(store.root)

    def tools(self, project: str | None = None) -> list[Tool]:
        """The tools of a project, each parameter with what it defaults to there."""
        github_page = self._store.project(project).github_page if project else ""
        return [
            tool.model_copy(
                update={
                    "parameters": [
                        parameter.model_copy(
                            update={"default": default_value(parameter, github_page)}
                        )
                        for parameter in tool.parameters
                    ]
                }
            )
            for tool in self._tools(project).values()
        ]

    def _tools(self, project: str | None) -> dict[str, Tool]:
        """The tools by id; a project's own tool files replace those of the root."""
        directory = Path(self._store.project(project).path) if project else None
        return load_tools(self._store.root, directory)

    # -- panes -------------------------------------------------------------

    def open_pane(
        self,
        project: str,
        task: str,
        request: PaneOpen,
        role: str | None = None,
        prompt: str = "",
        spawned_by: str = "",
        author: str = "user",
    ) -> Pane | LogEntry | None:
        """Add a pane to the task's layout. Its program starts when a terminal attaches.

        An external tool gets no pane: its program is started at once, in a window of its own.
        A log tool gets none either: it adds an entry by `author` to the task's log.
        An agent gets a name with its `role`; without one, the first agent of the task is
        its master. `prompt` and `spawned_by` are for an agent that another one starts.
        """
        tool = self._tools(project).get(request.tool)
        if tool is None:
            raise UnknownTool(request.tool)
        found = self._store.task(project, task)
        if found.status == BACKLOG:
            raise NotStarted(found.name)
        github_page = self._store.project(project).github_page
        values = _checked_values(tool, request.values, Path(found.path), github_page)
        if tool.kind == "external":
            launch(
                tool,
                external_command(tool, values),
                _directory(tool, found),
                pane_env(os.environ, sys.prefix),
                self._logs / f"{tool.id}.log",
            )
            return None
        if tool.kind == "log":
            entry = LogEntry(
                time=datetime.now(UTC).replace(microsecond=0), author=author, text=values["entry"]
            )
            self._store.add_log_entry(project, task, entry)
            return entry
        if tool.kind == "web":
            _page(
                tool,
                values,
                found,
                files="http://files",
                service="http://service",
                log="http://log",
            )
        opened: list[Pane] = []

        def change(workspace: Workspace) -> None:
            pane = Pane(
                id=f"p{workspace.next_pane}",
                tool=tool.id,
                kind=tool.kind,
                title=": ".join([tool.name, *values.values()]),
                values=values,
                session=str(uuid.uuid4()),
                prompt=prompt,
                spawned_by=spawned_by,
                hidden=request.hidden,
            )
            if tool.agent:
                _make_agent(workspace, pane, (project, task), tool, role)
            workspace.next_pane += 1
            workspace.panes[pane.id] = pane
            if not pane.hidden:
                workspace.tree = add_leaf(workspace.tree, pane.id, request.beside)
            opened.append(pane)

        self._store.change_workspace(project, task, change)
        return opened[0]

    def close_pane(self, project: str, task: str, pane: str) -> None:
        found = self._store.task(project, task)
        if pane not in found.workspace.panes:
            raise PaneNotFound(pane)
        if found.workspace.panes[pane].kind == "diff":
            _draft_file(found, pane).unlink(missing_ok=True)
        self._end([(project, task, pane)])
        self._store.change_workspace(project, task, lambda workspace: _drop(workspace, pane))

    def set_tree(self, project: str, task: str, tree: Split | str | None) -> None:
        """Take over a layout the user arranged; it must hold exactly the task's shown panes."""

        def change(workspace: Workspace) -> None:
            shown = [pane.id for pane in workspace.panes.values() if not pane.hidden]
            if sorted(leaves(tree)) != sorted(shown):
                raise InvalidLayout(
                    "the layout must hold every shown pane of the task exactly once"
                )
            workspace.tree = tree

        self._store.change_workspace(project, task, change)

    def maximize(self, project: str, task: str, pane: str | None) -> None:
        def change(workspace: Workspace) -> None:
            if pane is not None and pane not in workspace.panes:
                raise PaneNotFound(pane)
            if pane is not None and workspace.panes[pane].hidden:
                raise InvalidLayout(f"{pane} is hidden; show it before maximizing it")
            workspace.maximized = pane

        self._store.change_workspace(project, task, change)

    def set_hidden(
        self, project: str, task: str, pane: str, hidden: bool, beside: str | None = None
    ) -> None:
        """Take a pane out of the layout, or put it back beside the pane `beside`.

        A hidden pane's program runs on.
        """

        def change(workspace: Workspace) -> None:
            if pane not in workspace.panes:
                raise PaneNotFound(pane)
            if hidden:
                _hide(workspace, pane)
            else:
                _show(workspace, pane, beside)

        self._store.change_workspace(project, task, change)

    def list_hidden(self, project: str, task: str, open: bool) -> None:
        """Unfold or fold the list of the task's hidden panes in the tasks bar."""

        def change(workspace: Workspace) -> None:
            workspace.hidden_open = open

        self._store.change_workspace(project, task, change)

    def highlight(
        self, project: str, task: str, pane: str, on: bool, beside: str | None = None
    ) -> None:
        """Highlight a pane, or end its highlight. A hidden pane is shown beside `beside`."""

        def change(workspace: Workspace) -> None:
            if pane not in workspace.panes:
                raise PaneNotFound(pane)
            workspace.panes[pane].highlighted = on
            if on:
                _show(workspace, pane, beside)

        self._store.change_workspace(project, task, change)

    def move(self, project: str, task: str, pane: str, beside: str) -> None:
        """Place a pane next to another one, splitting that one's place."""

        def change(workspace: Workspace) -> None:
            for wanted in (pane, beside):
                if wanted not in workspace.panes:
                    raise PaneNotFound(wanted)
            if pane == beside:
                raise InvalidLayout("a pane cannot be placed next to itself")
            for wanted in (pane, beside):
                if workspace.panes[wanted].hidden:
                    raise InvalidLayout(f"{wanted} is hidden; show it first")
            workspace.tree = add_leaf(remove_leaf(workspace.tree, pane), pane, beside)

        self._store.change_workspace(project, task, change)

    # -- calls from the programs in the panes ------------------------------

    def caller(self, key: str) -> Key | None:
        """The pane whose program calls with this key; None if no running program has it."""
        with self._lock:
            return next(
                (
                    pane
                    for pane, known in self._keys.items()
                    if secrets.compare_digest(known.encode(), key.encode())
                ),
                None,
            )

    def open_for(self, caller: Key, call: PaneCall) -> Pane | LogEntry | None:
        """Open a tool for the program of a pane, beside that pane, and start it at once.

        A log entry is written by the pane's agent; a pane without one is used by the user.
        """
        project, task, pane = caller
        tool = self._tools(project).get(call.tool)
        if tool is None:
            raise UnknownTool(call.tool)
        if "agent" not in tool.callers:
            raise NotForAgents(tool.id)
        request = PaneOpen(
            tool=tool.id,
            values=values_from_args(tool, call.args),
            beside=call.beside or pane,
            hidden=call.hidden,
        )
        calling = self._store.task(project, task).workspace.panes.get(pane)
        author = calling.agent if calling and calling.agent else "user"
        opened = self.open_pane(project, task, request, author=author)
        if isinstance(opened, Pane):
            self.start(project, task, opened.id)
        return opened

    def spawn(self, caller: Key, request: AgentSpawn) -> Pane:
        """Start an agent with a prompt, in a hidden pane of its own; shown, it goes beside
        the calling one.

        It runs the calling pane's tool if that starts agents, the Claude tool otherwise.
        """
        project, task, pane = caller
        calling = self._store.task(project, task).workspace.panes.get(pane)
        tools = self._tools(project)
        tool = tools.get(calling.tool) if calling else None
        if tool is None or not (tool.agent and tool.spawn):
            tool = tools.get(SPAWNING_TOOL)
        if tool is None or not (tool.agent and tool.spawn):
            raise NoAgentTool()
        opened = self.open_pane(
            project,
            task,
            PaneOpen(tool=tool.id, beside=request.beside or pane, hidden=True),
            role=request.role,
            prompt=request.prompt,
            spawned_by=calling.agent if calling else "",
        )
        assert isinstance(opened, Pane)  # an agent tool is never external, nor a log
        self.start(project, task, opened.id)
        return opened

    def request_review(self, caller: Key, request: ReviewRequest) -> Pane:
        """Open a highlighted diff pane beside the calling one; the review the user writes
        there is kept for the caller instead of being sent to the master."""
        project, task, pane = caller
        tools = self._tools(project)
        tool = tools.get("diff")
        if tool is None or tool.kind != "diff":
            tool = next((other for other in tools.values() if other.kind == "diff"), None)
        if tool is None:
            raise NoDiffTool()
        opened = self.open_pane(project, task, PaneOpen(tool=tool.id, beside=pane))
        assert isinstance(opened, Pane)

        def change(workspace: Workspace) -> None:
            shown = workspace.panes[opened.id]
            shown.values = {"requester": pane, "mode": request.mode, "note": request.note}
            shown.highlighted = True

        self._store.change_workspace(project, task, change)
        return opened

    def review_state(self, caller: Key, pane: str) -> ReviewState:
        """Whether the review in a diff pane is finished; a finished one is handed out once."""
        project, task, _ = caller
        key = (project, task, pane)
        with self._lock:
            if key in self._reviews:
                return ReviewState(done=True, text=self._reviews.pop(key))
        if pane in self._store.task(project, task).workspace.panes:
            return ReviewState(done=False)
        return ReviewState(done=True, text=CLOSED_REVIEW)

    def diff(self, project: str, task: str, pane: str, mode: DiffMode) -> Diff:
        """The changes a diff pane shows."""
        found, _, tool = self._diff_pane(project, task, pane)
        try:
            return diffs.diff(_directory(tool, found), mode)
        except (diffs.GitFailed, OSError) as error:
            raise InvalidValues(f"The changes cannot be read: {error}") from error

    def draft(self, project: str, task: str, pane: str) -> Review:
        """The comments the user has written in a diff pane so far."""
        found, shown, _ = self._diff_pane(project, task, pane)
        file = _draft_file(found, pane)
        if file.is_file():
            try:
                return Review.model_validate_json(file.read_text())
            except ValueError:
                pass  # a damaged draft: start anew
        return Review(mode=shown.values.get("mode") or "branch")

    def save_draft(self, project: str, task: str, pane: str, review: Review) -> None:
        found, _, _ = self._diff_pane(project, task, pane)
        file = _draft_file(found, pane)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(review.model_dump_json(indent=2))

    def submit_review(self, project: str, task: str, pane: str, review: Review) -> None:
        """Send a finished review: to the agent that asked for it, or else to the master.

        The diff pane closes afterwards.
        """
        found, shown, tool = self._diff_pane(project, task, pane)
        try:
            _, label = diffs.base(_directory(tool, found), review.mode)
        except (diffs.GitFailed, OSError):
            label = "HEAD" if review.mode == "uncommitted" else "the default branch"
        text = diffs.format_review(label, review.comments, review.summary)
        requester = shown.values.get("requester")
        if requester:
            with self._lock:
                self._reviews[(project, task, pane)] = text
        else:
            self.send_to_master(project, task, text)
        count = len(review.comments)
        entry = LogEntry(
            time=datetime.now(UTC).replace(microsecond=0),
            author="user",
            text=f"Finished a review with {count} comment{'s' * (count != 1)}",
        )
        self._store.add_log_entry(project, task, entry)
        _draft_file(found, pane).unlink(missing_ok=True)
        self.close_pane(project, task, pane)

    def send_to_master(self, project: str, task: str, text: str) -> None:
        """Type a message into the terminal of the task's master, as if the user wrote it."""
        master = self._store.task(project, task).workspace.master
        with self._lock:
            pty = self._ptys.get((project, task, master)) if master else None
            if pty is None or pty.exit_code is not None:
                raise NoMaster()
        clean = text.replace("\x1b", "").replace("\r\n", "\n").encode()
        pty.write(PASTE_START + clean + PASTE_END)
        submit = threading.Timer(SUBMIT_DELAY_SECONDS, pty.write, args=(b"\r",))
        submit.daemon = True
        submit.start()

    def _diff_pane(self, project: str, task: str, pane: str) -> tuple[Task, Pane, Tool]:
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is None or shown.kind != "diff":
            raise PaneNotFound(pane)
        tool = self._tools(project).get(shown.tool)
        if tool is None or tool.kind != "diff":
            raise UnknownTool(shown.tool)
        return found, shown, tool

    def agents(self, project: str, task: str) -> list[Agent]:
        workspace = self._store.task(project, task).workspace
        with self._lock:
            return [
                Agent(
                    name=pane.agent,
                    pane=pane.id,
                    master=pane.id == workspace.master,
                    running=self._running((project, task, pane.id)),
                )
                for pane in workspace.panes.values()
                if pane.agent
            ]

    def _running(self, key: Key) -> bool:
        pty = self._ptys.get(key)
        return pty is not None and pty.exit_code is None

    # -- programs ----------------------------------------------------------

    def attach(self, project: str, task: str, pane: str, rows: int, cols: int) -> Pty | None:
        """The program of a pane, started now if the pane is new.

        None if the pane's program ended with an error in an earlier run.
        """
        with self._lock:
            found = self._store.task(project, task).workspace.panes.get(pane)
            if found is None or found.kind != "terminal":
                raise PaneNotFound(pane)
            key = (project, task, pane)
            if key not in self._ptys and found.exit_code is None:
                self._start(key, found, rows, cols, restoring=False)
            return self._ptys.get(key)

    def start(self, project: str, task: str, pane: str) -> None:
        """Start the program of a new terminal pane now, not when the window first shows it."""
        with self._lock:
            found = self._store.task(project, task).workspace.panes[pane]
            key = (project, task, pane)
            if found.kind == "terminal" and key not in self._ptys:
                self._start(key, found, found.rows, found.cols, restoring=False)

    def resized(self, project: str, task: str, pane: str, rows: int, cols: int) -> None:
        """Remember the size, so a restored program starts at the size it had."""

        def change(workspace: Workspace) -> None:
            if pane in workspace.panes:
                workspace.panes[pane].rows = rows
                workspace.panes[pane].cols = cols

        self._store.change_workspace(project, task, change)

    def start_all(self) -> None:
        """Start the program of every pane of every task, as after a restart."""
        with self._lock:
            for project in self._store.app_state().projects:
                for task in project.tasks:
                    for pane in task.workspace.panes.values():
                        key = (project.slug, task.slug, pane.id)
                        if pane.kind != "terminal":
                            continue  # a page is loaded by the pane that shows it
                        if key not in self._ptys and pane.exit_code is None:
                            self._start(key, pane, pane.rows, pane.cols, restoring=True)

    def _start(self, key: Key, pane: Pane, rows: int, cols: int, restoring: bool) -> None:
        project, task, pane_id = key
        tool = self._tools(project).get(pane.tool)
        if tool is None:
            self._ended(key, COULD_NOT_START)
            return
        if tool.agent and not pane.agent:  # opened before its tool started agents
            self._store.change_workspace(
                project,
                task,
                lambda workspace: _make_agent(
                    workspace, workspace.panes[pane_id], (project, task), tool, None
                ),
            )
        found = self._store.task(project, task)
        pane = found.workspace.panes[pane_id]
        instructions = write_instructions(self._store.root, found, pane) if tool.agent else None
        line = command_line(tool, pane, restoring, instructions)
        argv = [*self._shell, "-c", line] if line else self._shell
        self._ptys[key] = Pty(
            argv,
            _directory(tool, found),
            self._env(key),
            rows,
            cols,
            on_exit=lambda code: self._ended(key, code),
        )

    def _env(self, key: Key) -> dict[str, str]:
        """The environment of a pane's program: the user's, and how to reach the core.

        The key it gets lets the core tell which pane calls; it lasts as long as the program.
        """
        project, task, pane = key
        self._keys[key] = secrets.token_urlsafe(24)
        env = pane_env(os.environ, sys.prefix)
        env["PATH"] = os.pathsep.join(filter(None, [str(self._bin), env["PATH"]]))
        env.update(
            BYTEARMY_URL=self.url,
            BYTEARMY_TOKEN=self._keys[key],
            BYTEARMY_TASK=f"{project}/{task}",
            BYTEARMY_PANE=pane,
        )
        return env

    def page(self, project: str, task: str, pane: str, files: str, log: str = "") -> str:
        """The page a web pane shows; `files` is where the task directory is served, `log`
        where the task's log is shown.

        Starts the service of the pane's tool if it does not run yet, which can take a while.
        """
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is None or shown.kind != "web":
            raise PaneNotFound(pane)
        tool = self._tools(project).get(shown.tool)
        if tool is None or tool.kind != "web":
            raise UnknownTool(shown.tool)
        service = ""
        if tool.service is not None:
            service = self._services.url(tool, pane_env(os.environ, sys.prefix))
        return _page(tool, shown.values, found, files, service, log)

    def page_version(self, project: str, task: str, pane: str) -> str | None:
        """A text that changes when a file the web pane shows changes; None if it shows none.

        The files a pane shows are the values of its tool's path parameters, and the
        task's log if its page is the log.
        """
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is None or shown.kind != "web":
            raise PaneNotFound(pane)
        tool = self._tools(project).get(shown.tool)
        files = [
            Path(found.path) / shown.values[parameter.name]
            for parameter in (tool.parameters if tool else [])
            if parameter.path and parameter.name in shown.values
        ]
        if tool and "{log}" in tool.url:
            # The log page has the colors of the window.
            files.append(Path(found.path) / METADATA_DIR / LOG_FILE)
            return " ".join([*(_stamp(file) for file in files), themes.stamp(self._store.root)])
        return " ".join(_stamp(file) for file in files) if files else None

    def _ended(self, key: Key, code: int) -> None:
        """A program ended by itself: close its pane if all went well, keep it otherwise."""
        project, task, pane = key
        with self._lock:
            self._keys.pop(key, None)
            if self._stopping or key in self._ending:
                return

            def change(workspace: Workspace) -> None:
                if pane not in workspace.panes:
                    return
                if code == 0:
                    _drop(workspace, pane)
                else:
                    workspace.panes[pane].exit_code = code

            if code == 0:
                self._ptys.pop(key, None)
            try:
                self._store.change_workspace(project, task, change)
            except (ProjectNotFound, TaskNotFound):
                pass  # deleted meanwhile

    def _end(self, keys: list[Key]) -> None:
        """End programs on purpose and wait for them; their panes are not touched."""
        with self._lock:
            self._ending.update(keys)
            ptys = [pty for key in keys if (pty := self._ptys.pop(key, None)) is not None]
        for pty in ptys:  # outside the lock: an ending program reports through `_ended`
            pty.hang_up()
        for pty in ptys:
            pty.kill()
        with self._lock:
            self._ending.difference_update(keys)

    def stop(self, project: str, task: str | None = None) -> None:
        """End the programs of one task, or of every task of the project."""
        with self._lock:
            keys = [key for key in self._ptys if key[0] == project and task in (None, key[1])]
        self._end(keys)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            keys = list(self._ptys)
        self._end(keys)
        self._services.shutdown()


def _directory(tool: Tool, task: Task) -> Path:
    """Where a tool starts: the clone if it asks for it and the task has one."""
    directory = Path(task.path)
    if tool.directory == "repo" and task.clone == "ready":
        directory /= task.repo
    return directory


def _draft_file(task: Task, pane: str) -> Path:
    return Path(task.path) / METADATA_DIR / REVIEWS_DIR / f"{pane}.json"


def _stamp(file: Path) -> str:
    """Differs after the file was written again; time and size, as either can stay the same."""
    try:
        status = file.stat()
    except OSError:
        return "missing"
    return f"{status.st_mtime_ns}-{status.st_size}"


def _page(
    tool: Tool, values: Mapping[str, str], task: Task, files: str, service: str, log: str
) -> str:
    try:
        return page_url(tool, values, _directory(tool, task), files, service, log)
    except ValueError as error:
        raise InvalidValues(str(error)) from error


def _hide(workspace: Workspace, pane: str) -> None:
    workspace.panes[pane].hidden = True
    workspace.tree = remove_leaf(workspace.tree, pane)
    if workspace.maximized == pane:
        workspace.maximized = None


def _show(workspace: Workspace, pane: str, beside: str | None) -> None:
    """Put a hidden pane back into the layout, splitting `beside`; a shown one stays put."""
    if workspace.panes[pane].hidden:
        workspace.panes[pane].hidden = False
        workspace.tree = add_leaf(workspace.tree, pane, beside)


def _drop(workspace: Workspace, pane: str) -> None:
    workspace.panes.pop(pane, None)
    workspace.tree = remove_leaf(workspace.tree, pane)
    if workspace.maximized == pane:
        workspace.maximized = None
    if workspace.master == pane:
        workspace.master = None


def _make_agent(
    workspace: Workspace, pane: Pane, place: tuple[str, str], tool: Tool, role: str | None
) -> None:
    """Name the agent of a pane. Without a role it is the master, if the task has none."""
    prefix = agent_name(*place, "")
    roles = {other.agent.removeprefix(prefix) for other in workspace.panes.values() if other.agent}
    if role is None:
        role = next_role(roles) if workspace.master is not None else MASTER
    if role in roles:
        raise RoleTaken(role)
    pane.agent = agent_name(*place, role)
    pane.title = f"{tool.name}: {role}"
    if role == MASTER:
        workspace.master = pane.id


def values_from_args(tool: Tool, args: Sequence[str]) -> dict[str, str]:
    """The parameter values a command line gives: `name=value`, or by position."""
    names = [parameter.name for parameter in tool.parameters]
    values: dict[str, str] = {}
    by_position = []
    for arg in args:
        name, equals, value = arg.partition("=")
        if equals and name in names and name not in values:
            values[name] = value
        else:
            by_position.append(arg)
    free = [name for name in names if name not in values]
    if len(by_position) > len(free):
        takes = ", ".join(names) or "nothing"
        raise InvalidValues(f"{tool.id} takes {takes}; got {' '.join(args)}")
    values.update(zip(free, by_position, strict=False))
    return values


def _checked_values(
    tool: Tool, given: Mapping[str, str], task_dir: Path, github_page: str
) -> dict[str, str]:
    """The values of the tool's parameters; every parameter needs one."""
    values = {}
    for parameter in tool.parameters:
        value = given.get(parameter.name, "").strip() or default_value(parameter, github_page)
        if not value:
            raise InvalidValues(f"{parameter.label or parameter.name} is required.")
        if parameter.path and not (task_dir / value).resolve().is_relative_to(task_dir.resolve()):
            raise InvalidValues(f"{value} is not inside the task directory.")
        values[parameter.name] = value
    return values
