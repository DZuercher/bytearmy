"""The data bytearmy keeps on disk and exchanges with the UI."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class ProjectMeta(BaseModel):
    """Contents of `<project>/.bytearmy/project.json`."""

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


class Project(ProjectMeta):
    slug: str  # directory name below the root; the project's id
    path: str


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


class Focus(BaseModel):
    slug: str | None
