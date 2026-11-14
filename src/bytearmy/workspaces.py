"""The workspaces of the started tasks: their panes and the programs running in them."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from bytearmy.layout import add_leaf, leaves, remove_leaf
from bytearmy.models import BACKLOG, Pane, PaneOpen, Split, Task, Tool, Workspace
from bytearmy.ptys import Pty
from bytearmy.services import Services, launch
from bytearmy.store import METADATA_DIR, ProjectNotFound, Store, TaskNotFound
from bytearmy.tools import (
    command_line,
    default_value,
    ensure_premade,
    external_command,
    load_tools,
    page_url,
)

COULD_NOT_START = 127  # the exit code a shell gives for a command it cannot find

Key = tuple[str, str, str]  # project, task, pane


class UnknownTool(LookupError):
    pass


class PaneNotFound(LookupError):
    pass


class NotStarted(ValueError):
    """args: the name of a task that is still in the backlog."""


class InvalidValues(ValueError):
    pass


class InvalidLayout(ValueError):
    pass


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


class Workspaces:
    def __init__(self, store: Store, shell: Sequence[str] | None = None) -> None:
        self._store = store
        # A login shell that reads the user's startup files: functions and aliases work.
        self._shell = list(shell or [os.environ.get("SHELL", "/bin/sh"), "-l", "-i"])
        self._lock = threading.RLock()
        self._ptys: dict[Key, Pty] = {}
        self._ending: set[Key] = set()  # panes whose program is being ended on purpose
        self._stopping = False
        self._logs = store.root / METADATA_DIR / "logs"  # of programs without a terminal
        self._services = Services(self._logs)
        ensure_premade(store.root)

    def tools(self, github_page: str = "") -> list[Tool]:
        """The tools, each parameter with what it defaults to in a project with that page."""
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
            for tool in load_tools(self._store.root).values()
        ]

    # -- panes -------------------------------------------------------------

    def open_pane(self, project: str, task: str, request: PaneOpen) -> Pane | None:
        """Add a pane to the task's layout. Its program starts when a terminal attaches.

        An external tool gets no pane: its program is started at once, in a window of its own.
        """
        tool = load_tools(self._store.root).get(request.tool)
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
        if tool.kind == "web":
            _page(tool, values, found, files="http://files", service="http://service")
        opened: list[Pane] = []

        def change(workspace: Workspace) -> None:
            pane = Pane(
                id=f"p{workspace.next_pane}",
                tool=tool.id,
                kind=tool.kind,
                title=": ".join([tool.name, *values.values()]),
                values=values,
                session=str(uuid.uuid4()),
            )
            workspace.next_pane += 1
            workspace.panes[pane.id] = pane
            workspace.tree = add_leaf(workspace.tree, pane.id, request.beside)
            opened.append(pane)

        self._store.change_workspace(project, task, change)
        return opened[0]

    def close_pane(self, project: str, task: str, pane: str) -> None:
        if pane not in self._store.task(project, task).workspace.panes:
            raise PaneNotFound(pane)
        self._end([(project, task, pane)])
        self._store.change_workspace(project, task, lambda workspace: _drop(workspace, pane))

    def set_tree(self, project: str, task: str, tree: Split | str | None) -> None:
        """Take over a layout the user arranged; it must hold exactly the task's panes."""

        def change(workspace: Workspace) -> None:
            if sorted(leaves(tree)) != sorted(workspace.panes):
                raise InvalidLayout("the layout must hold every pane of the task exactly once")
            workspace.tree = tree

        self._store.change_workspace(project, task, change)

    def maximize(self, project: str, task: str, pane: str | None) -> None:
        def change(workspace: Workspace) -> None:
            if pane is not None and pane not in workspace.panes:
                raise PaneNotFound(pane)
            workspace.maximized = pane

        self._store.change_workspace(project, task, change)

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
        project, task, _ = key
        tool = load_tools(self._store.root).get(pane.tool)
        if tool is None:
            self._ended(key, COULD_NOT_START)
            return
        line = command_line(tool, pane, restoring)
        argv = [*self._shell, "-c", line] if line else self._shell
        self._ptys[key] = Pty(
            argv,
            _directory(tool, self._store.task(project, task)),
            pane_env(os.environ, sys.prefix),
            rows,
            cols,
            on_exit=lambda code: self._ended(key, code),
        )

    def page(self, project: str, task: str, pane: str, files: str) -> str:
        """The page a web pane shows; `files` is where the task directory is served.

        Starts the service of the pane's tool if it does not run yet, which can take a while.
        """
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is None or shown.kind != "web":
            raise PaneNotFound(pane)
        tool = load_tools(self._store.root).get(shown.tool)
        if tool is None or tool.kind != "web":
            raise UnknownTool(shown.tool)
        service = ""
        if tool.service is not None:
            service = self._services.url(tool, pane_env(os.environ, sys.prefix))
        return _page(tool, shown.values, found, files, service)

    def _ended(self, key: Key, code: int) -> None:
        """A program ended by itself: close its pane if all went well, keep it otherwise."""
        project, task, pane = key
        with self._lock:
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


def _page(tool: Tool, values: Mapping[str, str], task: Task, files: str, service: str) -> str:
    try:
        return page_url(tool, values, _directory(tool, task), files, service)
    except ValueError as error:
        raise InvalidValues(str(error)) from error


def _drop(workspace: Workspace, pane: str) -> None:
    workspace.panes.pop(pane, None)
    workspace.tree = remove_leaf(workspace.tree, pane)
    if workspace.maximized == pane:
        workspace.maximized = None


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
