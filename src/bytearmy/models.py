"""The data bytearmy keeps on disk and exchanges with the UI."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BACKLOG = "Backlog"
NEW = "New"


class ProjectMeta(BaseModel):
    """What the user says about a project."""

    name: str
    description: str = ""
    github_url: str = ""

    @field_validator("name", "description", "github_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a project name must be provided")
        return value


class ProjectFile(ProjectMeta):
    """Contents of `<project>/.bytearmy/project.json`."""

    backlog_order: list[str] = []
    task_order: list[str] = []
    focused_task: str | None = None


class TaskCreate(BaseModel):
    """What the user says about a task."""

    name: str
    description: str = ""
    branch: str = ""
    reminder: date | None = None
    dependencies: list[str] = []  # slugs of tasks in the same project

    @field_validator("name", "description", "branch")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a task name must be provided")
        return value


class TaskFile(TaskCreate):
    """Contents of `<task>/.bytearmy/task.json`."""

    status: str = BACKLOG
    repo: str = ""  # directory of the clone below the task directory; empty without a GitHub URL
    clone_error: str = ""


class Split(BaseModel):
    """A node of a workspace's layout tree, in the form react-mosaic draws."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["split"] = "split"
    direction: Literal["row", "column"]
    children: list[Split | str]  # a string is a leaf: the id of a pane
    split_percentages: list[float] | None = Field(default=None, alias="splitPercentages")


class Pane(BaseModel):
    id: str
    tool: str
    kind: Literal["terminal", "web"] = "terminal"  # a program in a terminal, or a page
    title: str
    values: dict[str, str] = {}  # the tool's parameters, e.g. the file for Vim
    session: str = ""  # stays the same across restarts; lets a tool such as Claude resume
    rows: int = 24
    cols: int = 80
    exit_code: int | None = None  # set when the program ended with an error


class Workspace(BaseModel):
    """Contents of `<task>/.bytearmy/layout.json`."""

    tree: Split | str | None = None
    panes: dict[str, Pane] = {}
    maximized: str | None = None
    next_pane: int = 1


class Task(TaskFile):
    slug: str  # directory name below the project; the task's id within it
    path: str
    clone: Literal["none", "cloning", "ready", "failed"]
    reminder_due: bool
    blocked_by: list[str]  # names of the dependencies that are still in the backlog
    workspace: Workspace


class Parameter(BaseModel):
    name: str
    label: str = ""
    path: bool = False  # the value is a file below the task directory
    default: str = ""  # used if nothing is entered; {github_page} is the project's page


class Service(BaseModel):
    """A program in the background that all panes of a web tool share."""

    command: str  # run by /bin/sh; {port} and {token} are chosen by bytearmy
    url: str  # where it then answers, e.g. `http://127.0.0.1:{port}/?tkn={token}`


class Tool(BaseModel):
    """A tool the palette offers; one file in `<root>/.tools`."""

    id: str
    name: str
    # terminal: a program in a terminal pane. web: a page in a browser pane.
    # external: a program with a window of its own, no pane.
    kind: Literal["terminal", "web", "external"] = "terminal"
    command: str = ""  # run by the user's shell; for a terminal, empty means the shell itself
    resume: str = ""  # run instead of `command` when a pane is restored after a restart
    url: str = ""  # the page of a web tool
    directory: Literal["task", "repo"] = "task"  # repo: the clone, if the task has one
    callers: list[Literal["user", "agent"]] = ["user", "agent"]  # who may open the tool
    parameters: list[Parameter] = []
    service: Service | None = None

    @model_validator(mode="after")
    def _complete(self) -> Tool:
        if self.kind == "web" and not self.url:
            raise ValueError("a web tool needs a url")
        if self.kind == "external" and not self.command:
            raise ValueError("an external tool needs a command")
        return self


class Project(ProjectMeta):
    slug: str  # directory name below the root; the project's id
    path: str
    github_page: str  # the GitHub URL as a browser can show it; empty if it has none
    backlog: list[Task]
    tasks: list[Task]
    focused_task: str | None


class UiState(BaseModel):
    left_open: bool = True
    right_open: bool = True
    bottom_open: bool = True


class UiChange(BaseModel):
    """The bars to open or close; a bar that is not named keeps its state."""

    left_open: bool | None = None
    right_open: bool | None = None
    bottom_open: bool | None = None


class GlobalState(BaseModel):
    """Contents of `<root>/.bytearmy/state.json`."""

    project_order: list[str] = []
    focused_project: str | None = None
    ui: UiState = UiState()


class AppState(BaseModel):
    """Everything the UI shows, in one piece."""

    root: str
    projects: list[Project]
    focused_project: str | None
    ui: UiState
    tools: list[Tool] = []


class ProjectOrder(BaseModel):
    order: list[str]


class TaskOrder(BaseModel):
    bar: Literal["backlog", "tasks"]
    order: list[str]


class Focus(BaseModel):
    slug: str | None


class PaneOpen(BaseModel):
    tool: str
    values: dict[str, str] = {}
    beside: str | None = None  # the pane to split; the last pane if not given


class Layout(BaseModel):
    tree: Split | str | None


class Page(BaseModel):
    """What a web pane shows."""

    url: str


class PageVersion(BaseModel):
    """Tells a web pane when to load its page again."""

    version: str | None  # changes with the files the page shows; None if it shows none
