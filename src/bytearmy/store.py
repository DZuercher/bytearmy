"""The file system as bytearmy's database.

Layout below the root:

    .bytearmy/state.json            project order, focused project, UI state
    <slug>/.bytearmy/project.json   name, description, GitHub URL of one project

A directory is a project if and only if it holds that metadata file.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path

from pydantic import BaseModel, ValidationError

from bytearmy.models import AppState, GlobalState, Project, ProjectMeta, UiChange

log = logging.getLogger(__name__)

METADATA_DIR = ".bytearmy"
MAX_SLUG_LENGTH = 64
FALLBACK_SLUG = "project"
SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class ProjectNotFound(LookupError):
    pass


class InvalidOrder(ValueError):
    pass


class DuplicateName(ValueError):
    pass


def slugify(name: str) -> str:
    """A directory name for a project name: `Project 1` becomes `project-1`."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-") or FALLBACK_SLUG


def unique_slug(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`, ... if the name is taken."""
    slug, counter = base, 1
    while slug in taken:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def _write_json(path: Path, model: BaseModel) -> None:
    # Write beside the target and rename: a crash never leaves a half-written file.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(model.model_dump_json(indent=2) + "\n")
    os.replace(temporary, path)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        (self.root / METADATA_DIR).mkdir(parents=True, exist_ok=True)

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

    def _read_projects(self) -> dict[str, Project]:
        projects = {}
        for entry in self.root.iterdir():
            try:
                directory = self._project_dir(entry.name)
                meta = ProjectMeta.model_validate_json(
                    (directory / METADATA_DIR / "project.json").read_text()
                )
            except ProjectNotFound:
                continue
            except (OSError, ValidationError) as error:
                log.warning("Skipping project %s: unreadable metadata (%s)", entry.name, error)
                continue
            projects[entry.name] = Project(slug=entry.name, path=str(directory), **meta.model_dump())
        return projects

    def _list_projects(self, state: GlobalState) -> list[Project]:
        """Projects in the stored order; ones the order does not know come last."""
        projects = self._read_projects()
        order = [slug for slug in state.project_order if slug in projects]
        order += sorted(projects.keys() - set(order))
        return [projects[slug] for slug in order]

    def create_project(self, meta: ProjectMeta) -> Project:
        with self._lock:
            if any(project.name == meta.name for project in self._read_projects().values()):
                raise DuplicateName(meta.name)
            # Different names can still share a slug, and other directories can hold it.
            # Compare in lower case: macOS file systems ignore case, Linux ones do not.
            taken = {entry.name.lower() for entry in self.root.iterdir()}
            slug = unique_slug(slugify(meta.name), taken)
            directory = self.root / slug
            (directory / METADATA_DIR).mkdir(parents=True)
            _write_json(directory / METADATA_DIR / "project.json", meta)

            state = self._load_state()
            others = [p.slug for p in self._list_projects(state) if p.slug != slug]
            state.project_order = [*others, slug]
            state.focused_project = slug
            self._save_state(state)
            return Project(slug=slug, path=str(directory), **meta.model_dump())

    def delete_project(self, slug: str) -> None:
        """Remove the project's directory with everything in it.

        The stored order and focus need no cleanup: reading them drops
        whatever is no longer on disk.
        """
        with self._lock:
            shutil.rmtree(self._project_dir(slug))

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
