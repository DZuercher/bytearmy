"""The file system as bytearmy's database.

Layout below the root:

    .bytearmy/state.json                   project order, focused project, UI state
    <project>/.bytearmy/project.json       name, description, GitHub URL, task order, focused task
    <project>/<task>/.bytearmy/task.json   name, description, status, branch, reminder, dependencies
    <project>/<task>/<repo>/               clone of the project's GitHub URL, if it has one

A directory is a project or a task if and only if it holds that metadata file.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import unicodedata
from collections.abc import Callable, Collection
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from bytearmy.models import (
    BACKLOG,
    NEW,
    AppState,
    GlobalState,
    Project,
    ProjectFile,
    ProjectMeta,
    Task,
    TaskChange,
    TaskCreate,
    TaskFile,
    UiChange,
    Workspace,
)

log = logging.getLogger(__name__)

METADATA_DIR = ".bytearmy"
MAX_SLUG_LENGTH = 64
SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
INTERRUPTED_CLONE = "The clone did not finish."


class ProjectNotFound(LookupError):
    pass


class TaskNotFound(LookupError):
    pass


class InvalidOrder(ValueError):
    pass


class DuplicateName(ValueError):
    """args: what the name belongs to ("project" or "task"), then the name."""


class UnknownDependency(ValueError):
    pass


class Blocked(ValueError):
    """args: the task's name, then the names of the dependencies it waits for."""


def slugify(name: str, fallback: str = "project") -> str:
    """A directory name for a project or task name: `Project 1` becomes `project-1`."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-") or fallback


def unique_slug(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`, ... if the name is taken."""
    slug, counter = base, 1
    while slug in taken:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def repo_directory(url: str) -> str:
    """The directory `git clone <url>` would create: the URL's last part without `.git`."""
    name = re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")
    return name if REPO_RE.fullmatch(name) else "repo"


def browser_page(url: str) -> str:
    """The page a browser shows for a git URL; empty if the URL names no web host."""
    url = url.rstrip("/").removesuffix(".git")
    for pattern in (r"ssh://git@([^:/]+)(?::\d+)?/(.+)", r"git@([^:/]+):(.+)"):
        if match := re.fullmatch(pattern, url):
            return f"https://{match[1]}/{match[2]}"
    return url if url.startswith(("https://", "http://")) else ""


def _ordered(order: list[str], existing: Collection[str]) -> list[str]:
    """`existing` in the stored order; whatever the order does not know comes last."""
    known = [slug for slug in order if slug in existing]
    return known + sorted(set(existing) - set(known))


def _write_json(path: Path, model: BaseModel) -> None:
    # Write beside the target and rename: a crash never leaves a half-written file.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(model.model_dump_json(indent=2, by_alias=True) + "\n")
    os.replace(temporary, path)


class Store:
    def __init__(self, root: Path, today: Callable[[], date] = date.today) -> None:
        self.root = root
        self._today = today
        self._lock = threading.RLock()
        self._cloning: set[tuple[str, str]] = set()  # (project, task) with a clone under way
        self.version = 0  # counts the changes, so a window learns that it shows old data
        (self.root / METADATA_DIR).mkdir(parents=True, exist_ok=True)

    def _changed(self) -> None:
        with self._lock:
            self.version += 1

    # -- global state ------------------------------------------------------

    @property
    def _state_file(self) -> Path:
        return self.root / METADATA_DIR / "state.json"

    def _load_state(self) -> GlobalState:
        if not self._state_file.exists():
            return GlobalState()
        return GlobalState.model_validate_json(self._state_file.read_text())

    def _save_state(self, state: GlobalState) -> None:
        _write_json(self._state_file, state)
        self._changed()

    def app_state(self) -> AppState:
        with self._lock:
            state = self._load_state()
            projects = self._list_projects(state)
            focused = state.focused_project
            if focused not in {project.slug for project in projects}:
                focused = None
            return AppState(
                root=str(self.root), projects=projects, focused_project=focused, ui=state.ui
            )

    def update_ui(self, change: UiChange) -> None:
        with self._lock:
            state = self._load_state()
            state.ui = state.ui.model_copy(update=change.model_dump(exclude_none=True))
            self._save_state(state)

    # -- projects ----------------------------------------------------------

    def _project_dir(self, slug: str) -> Path:
        """The directory of an existing project; anything else is not a project."""
        directory = self.root / slug
        if (
            not SLUG_RE.fullmatch(slug)
            or directory.is_symlink()
            or not (directory / METADATA_DIR / "project.json").is_file()
        ):
            raise ProjectNotFound(slug)
        return directory

    def _project_file(self, directory: Path) -> ProjectFile:
        return ProjectFile.model_validate_json(
            (directory / METADATA_DIR / "project.json").read_text()
        )

    def _read_projects(self) -> dict[str, Project]:
        projects = {}
        for entry in self.root.iterdir():
            try:
                directory = self._project_dir(entry.name)
                file = self._project_file(directory)
            except ProjectNotFound:
                continue
            except (OSError, ValidationError) as error:
                log.warning("Skipping project %s: unreadable metadata (%s)", entry.name, error)
                continue
            backlog, tasks = self._list_tasks(directory, file)
            focused_task = file.focused_task
            if focused_task not in {task.slug for task in tasks}:
                focused_task = None
            projects[entry.name] = Project(
                slug=entry.name,
                path=str(directory),
                name=file.name,
                description=file.description,
                github_url=file.github_url,
                github_page=browser_page(file.github_url),
                backlog=backlog,
                tasks=tasks,
                focused_task=focused_task,
            )
        return projects

    def _list_projects(self, state: GlobalState) -> list[Project]:
        projects = self._read_projects()
        return [projects[slug] for slug in _ordered(state.project_order, projects)]

    def project(self, slug: str) -> Project:
        with self._lock:
            self._project_dir(slug)
            return self._read_projects()[slug]

    def create_project(self, meta: ProjectMeta) -> Project:
        with self._lock:
            projects = self._read_projects()
            if any(project.name == meta.name for project in projects.values()):
                raise DuplicateName("project", meta.name)
            # Different names can still share a slug, and other directories can hold it.
            # Compare in lower case: macOS file systems ignore case, Linux ones do not.
            taken = {entry.name.lower() for entry in self.root.iterdir()}
            slug = unique_slug(slugify(meta.name), taken)
            directory = self.root / slug
            (directory / METADATA_DIR).mkdir(parents=True)
            _write_json(directory / METADATA_DIR / "project.json", ProjectFile(**meta.model_dump()))

            state = self._load_state()
            state.project_order = [*_ordered(state.project_order, projects), slug]
            state.focused_project = slug
            self._save_state(state)
            return self._read_projects()[slug]

    def delete_project(self, slug: str) -> None:
        """Remove the project's directory with everything in it.

        The stored order and focus need no cleanup: reading them drops
        whatever is no longer on disk.
        """
        with self._lock:
            shutil.rmtree(self._project_dir(slug))
            self._changed()

    def reorder_projects(self, order: list[str]) -> None:
        with self._lock:
            if sorted(order) != sorted(self._read_projects()):
                raise InvalidOrder("the order must name every project exactly once")
            state = self._load_state()
            state.project_order = order
            self._save_state(state)

    def focus_project(self, slug: str | None) -> None:
        with self._lock:
            if slug is not None:
                self._project_dir(slug)
            state = self._load_state()
            state.focused_project = slug
            self._save_state(state)

    # -- tasks -------------------------------------------------------------

    def _task_dir(self, project_dir: Path, slug: str) -> Path:
        """The directory of an existing task; anything else is not a task."""
        directory = project_dir / slug
        if (
            not SLUG_RE.fullmatch(slug)
            or directory.is_symlink()
            or not (directory / METADATA_DIR / "task.json").is_file()
        ):
            raise TaskNotFound(slug)
        return directory

    def _task_files(self, project_dir: Path) -> dict[str, TaskFile]:
        files = {}
        for entry in project_dir.iterdir():
            try:
                directory = self._task_dir(project_dir, entry.name)
                files[entry.name] = TaskFile.model_validate_json(
                    (directory / METADATA_DIR / "task.json").read_text()
                )
            except TaskNotFound:
                continue
            except (OSError, ValidationError) as error:
                log.warning("Skipping task %s: unreadable metadata (%s)", entry.name, error)
        return files

    def _save_task(self, project_dir: Path, slug: str, file: TaskFile) -> None:
        _write_json(project_dir / slug / METADATA_DIR / "task.json", file)
        self._changed()

    def _save_project(self, project_dir: Path, file: ProjectFile) -> None:
        _write_json(project_dir / METADATA_DIR / "project.json", file)
        self._changed()

    def _list_tasks(self, project_dir: Path, project: ProjectFile) -> tuple[list[Task], list[Task]]:
        """The project's tasks as (backlog, task bar), each in its stored order."""
        files = self._task_files(project_dir)
        in_backlog = {slug for slug, file in files.items() if file.status == BACKLOG}
        today = self._today()

        def task(slug: str) -> Task:
            file = files[slug]
            directory = project_dir / slug
            clone, clone_error = self._clone_state(project_dir.name, slug, file, directory)
            return Task(
                **file.model_dump(exclude={"clone_error"}),
                slug=slug,
                path=str(directory),
                clone=clone,
                clone_error=clone_error,
                reminder_due=file.reminder is not None and file.reminder <= today,
                blocked_by=[
                    files[other].name for other in file.dependencies if other in in_backlog
                ],
                workspace=self._load_workspace(directory),
            )

        backlog = [task(slug) for slug in _ordered(project.backlog_order, in_backlog)]
        tasks = [task(slug) for slug in _ordered(project.task_order, files.keys() - in_backlog)]
        return backlog, tasks

    def _clone_state(
        self, project: str, slug: str, file: TaskFile, directory: Path
    ) -> tuple[Literal["none", "cloning", "ready", "failed"], str]:
        if (project, slug) in self._cloning:
            return "cloning", ""
        if file.clone_error:
            return "failed", file.clone_error
        if not file.repo:
            return "none", ""
        if (directory / file.repo / ".git").exists():
            return "ready", ""
        return "failed", INTERRUPTED_CLONE

    def task(self, project: str, slug: str) -> Task:
        with self._lock:
            project_dir = self._project_dir(project)
            self._task_dir(project_dir, slug)
            backlog, tasks = self._list_tasks(project_dir, self._project_file(project_dir))
            return next(task for task in [*backlog, *tasks] if task.slug == slug)

    def create_task(self, project: str, new: TaskCreate) -> Task:
        with self._lock:
            project_dir = self._project_dir(project)
            project_file = self._project_file(project_dir)
            files = self._task_files(project_dir)
            if any(file.name == new.name for file in files.values()):
                raise DuplicateName("task", new.name)
            for dependency in new.dependencies:
                if dependency not in files:
                    raise UnknownDependency(dependency)
            taken = {entry.name.lower() for entry in project_dir.iterdir()}
            slug = unique_slug(slugify(new.name, fallback="task"), taken)
            (project_dir / slug / METADATA_DIR).mkdir(parents=True)
            fields = new.model_dump()
            fields["dependencies"] = list(dict.fromkeys(new.dependencies))
            if project_file.github_url:
                fields["repo"] = repo_directory(project_file.github_url)
            self._save_task(project_dir, slug, TaskFile(**fields))

            in_backlog = [other for other, file in files.items() if file.status == BACKLOG]
            project_file.backlog_order = [*_ordered(project_file.backlog_order, in_backlog), slug]
            self._save_project(project_dir, project_file)
            return self.task(project, slug)

    def start_task(self, project: str, slug: str) -> None:
        """Move a task from the backlog to the end of the task bar."""
        with self._lock:
            project_dir = self._project_dir(project)
            self._task_dir(project_dir, slug)
            files = self._task_files(project_dir)
            file = files[slug]
            if file.status != BACKLOG:
                return
            waiting = [
                files[other].name
                for other in file.dependencies
                if other in files and files[other].status == BACKLOG
            ]
            if waiting:
                raise Blocked(file.name, waiting)
            started = [other for other in files if files[other].status != BACKLOG]
            file.status = NEW
            self._save_task(project_dir, slug, file)
            project_file = self._project_file(project_dir)
            project_file.task_order = [*_ordered(project_file.task_order, started), slug]
            self._save_project(project_dir, project_file)

    def delete_task(self, project: str, slug: str) -> None:
        """Remove the task's directory with everything in it."""
        with self._lock:
            project_dir = self._project_dir(project)
            shutil.rmtree(self._task_dir(project_dir, slug))
            self._changed()
            # Nothing can wait for a task that no longer exists.
            for other, file in self._task_files(project_dir).items():
                if slug in file.dependencies:
                    file.dependencies.remove(slug)
                    self._save_task(project_dir, other, file)

    def reorder_tasks(
        self, project: str, bar: Literal["backlog", "tasks"], order: list[str]
    ) -> None:
        with self._lock:
            project_dir = self._project_dir(project)
            project_file = self._project_file(project_dir)
            backlog, tasks = self._list_tasks(project_dir, project_file)
            members = backlog if bar == "backlog" else tasks
            if sorted(order) != sorted(task.slug for task in members):
                raise InvalidOrder("the order must name every task of the bar exactly once")
            if bar == "backlog":
                project_file.backlog_order = order
            else:
                project_file.task_order = order
            self._save_project(project_dir, project_file)

    def focus_task(self, project: str, slug: str | None) -> None:
        with self._lock:
            project_dir = self._project_dir(project)
            if slug is not None:
                self._task_dir(project_dir, slug)
            project_file = self._project_file(project_dir)
            project_file.focused_task = slug
            self._save_project(project_dir, project_file)

    def change_task(self, project: str, slug: str, change: TaskChange) -> None:
        """Take over what an agent changed about its task: the fields it sent."""
        with self._lock:
            project_dir = self._project_dir(project)
            self._task_dir(project_dir, slug)
            file = self._task_files(project_dir)[slug]
            for name in change.model_fields_set:
                setattr(file, name, getattr(change, name))
            self._save_task(project_dir, slug, file)

    # -- workspaces --------------------------------------------------------

    def _load_workspace(self, task_dir: Path) -> Workspace:
        file = task_dir / METADATA_DIR / "layout.json"
        if not file.exists():
            return Workspace()
        try:
            return Workspace.model_validate_json(file.read_text())
        except (OSError, ValidationError) as error:
            log.warning("Ignoring the layout of %s: unreadable (%s)", task_dir.name, error)
            return Workspace()

    def change_workspace(
        self, project: str, slug: str, change: Callable[[Workspace], None]
    ) -> Workspace:
        """Apply `change` to the task's workspace and store the result."""
        with self._lock:
            task_dir = self._task_dir(self._project_dir(project), slug)
            workspace = self._load_workspace(task_dir)
            change(workspace)
            _write_json(task_dir / METADATA_DIR / "layout.json", workspace)
            self._changed()
            return workspace

    # -- clones ------------------------------------------------------------

    def clone_started(self, project: str, slug: str) -> None:
        with self._lock:
            self._cloning.add((project, slug))
            self._changed()

    def clone_finished(self, project: str, slug: str, error: str) -> None:
        """Record the outcome; `error` is empty after a successful clone."""
        with self._lock:
            self._cloning.discard((project, slug))
            try:
                project_dir = self._project_dir(project)
                self._task_dir(project_dir, slug)
            except (ProjectNotFound, TaskNotFound):
                return  # deleted while the clone was running
            file = self._task_files(project_dir)[slug]
            file.clone_error = error
            self._save_task(project_dir, slug, file)
