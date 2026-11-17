"""The files of the root the settings dialogue edits: config.toml and the instructions of the
orchestrator, the masters and the other agents."""

from __future__ import annotations

from pathlib import Path

from bytearmy.agents import (
    AGENT_FILE,
    AGENT_INSTRUCTIONS,
    MASTER_FILE,
    MASTER_INSTRUCTIONS,
    ORCHESTRATOR_FILE,
    ORCHESTRATOR_INSTRUCTIONS,
)
from bytearmy.config import CONFIG_FILE, DEFAULT_TEXT, check_config
from bytearmy.models import SettingsFile

# id: label, the file below the root, what it holds while it is missing
FILES = {
    "config": ("Config", CONFIG_FILE, DEFAULT_TEXT),
    "orchestrator": ("Orchestrator prompt", ORCHESTRATOR_FILE, ORCHESTRATOR_INSTRUCTIONS),
    "master": ("Master prompt", MASTER_FILE, MASTER_INSTRUCTIONS),
    "agent": ("Agent prompt", AGENT_FILE, AGENT_INSTRUCTIONS),
}


class UnknownSettings(LookupError):
    pass


def read_settings(root: Path) -> list[SettingsFile]:
    files = []
    for key, (label, relative, default) in FILES.items():
        file = root / relative
        text = file.read_text() if file.is_file() else default
        files.append(SettingsFile(id=key, label=label, path=str(file), text=text, default=default))
    return files


def write_settings(root: Path, key: str, text: str) -> None:
    """Write one file. Raise ValueError, saying why, for a config.toml that would not hold."""
    if key not in FILES:
        raise UnknownSettings(key)
    if key == "config":
        check_config(text)
    file = root / FILES[key][1]
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
