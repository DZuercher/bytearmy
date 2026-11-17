"""The files the settings dialogue edits: those of the root, config.toml and the instructions
of the orchestrator, the masters and the other agents, and those of a project, its
config.toml and what it adds to the instructions of its task agents."""

from __future__ import annotations

from pathlib import Path

from bytearmy.agents import (
    AGENT_FILE,
    AGENT_INSTRUCTIONS,
    MASTER_FILE,
    MASTER_INSTRUCTIONS,
    ORCHESTRATOR_FILE,
    ORCHESTRATOR_INSTRUCTIONS,
    SCRATCHPAD_FILE,
)
from bytearmy.config import CONFIG_FILE, DEFAULT_TEXT, PROJECT_DEFAULT_TEXT, check_config
from bytearmy.models import SettingsFile

# id: label, the file below the root, what it holds while it is missing
FILES = {
    "config": ("Config", CONFIG_FILE, DEFAULT_TEXT),
    "orchestrator": ("Orchestrator prompt", ORCHESTRATOR_FILE, ORCHESTRATOR_INSTRUCTIONS),
    "master": ("Master prompt", MASTER_FILE, MASTER_INSTRUCTIONS),
    "agent": ("Agent prompt", AGENT_FILE, AGENT_INSTRUCTIONS),
}
# id: label, the file below a project, what it holds while it is missing. The prompts are
# appended to those of the root.
PROJECT_FILES = {
    "config": ("Config", CONFIG_FILE, PROJECT_DEFAULT_TEXT),
    "master": ("Master prompt", MASTER_FILE, ""),
    "agent": ("Agent prompt", AGENT_FILE, ""),
    "scratchpad": ("Scratchpad prompt", SCRATCHPAD_FILE, ""),
}


class UnknownSettings(LookupError):
    pass


def read_settings(root: Path, project: Path | None = None) -> list[SettingsFile]:
    """The files of the root, or of a project."""
    directory, files = (root, FILES) if project is None else (project, PROJECT_FILES)
    found = []
    for key, (label, relative, default) in files.items():
        file = directory / relative
        text = file.read_text() if file.is_file() else default
        found.append(SettingsFile(id=key, label=label, path=str(file), text=text, default=default))
    return found


def write_settings(root: Path, key: str, text: str, project: Path | None = None) -> None:
    """Write one file of the root, or of a project. Raise ValueError, saying why, for a
    config.toml that would not hold."""
    directory, files = (root, FILES) if project is None else (project, PROJECT_FILES)
    if key not in files:
        raise UnknownSettings(key)
    if key == "config":
        check_config(text, root_level=project is None)
    file = directory / files[key][1]
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
