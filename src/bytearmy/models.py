"""The data bytearmy keeps on disk and exchanges with the UI."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

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


class Task(TaskFile):
    slug: str  # directory name below the project; the task's id within it
    path: str
    clone: Literal["none", "cloning", "ready", "failed"]
    reminder_due: bool
    blocked_by: list[str]  # names of the dependencies that are still in the backlog


class Project(ProjectMeta):
    slug: str  # directory name below the root; the project's id
    path: str
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


class ProjectOrder(BaseModel):
    order: list[str]


class TaskOrder(BaseModel):
    bar: Literal["backlog", "tasks"]
    order: list[str]


class Focus(BaseModel):
    slug: str | None
