"""Trusting directories for Claude: an agent pane should start working at once, not ask
whether its directory is to be trusted.

Claude keeps what it trusts in its configuration file, `~/.claude.json` (or
`$CLAUDE_CONFIG_DIR/.claude.json`), as `projects.<directory>.hasTrustDialogAccepted`.
Outside a git repository a trusted parent is enough, but inside one Claude asks anyway, so
every directory is trusted by an entry of its own.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

TRUSTED = "hasTrustDialogAccepted"


def claude_config(env: Mapping[str, str] = os.environ) -> Path:
    """Where Claude keeps its configuration."""
    directory = env.get("CLAUDE_CONFIG_DIR")
    return Path(directory or Path.home()) / ".claude.json"


def trust_directory(directory: Path, config: Path | None = None) -> None:
    """Mark `directory` as trusted in Claude's configuration, unless it already is.

    The file is only written when something changes, and then beside it and renamed, so
    Claude never reads half of it. A file that cannot be read or written is left alone:
    Claude then asks, as it would without bytearmy.
    """
    config = config or claude_config()
    directory = directory.resolve()
    try:
        settings = json.loads(config.read_text()) if config.exists() else {}
        if not isinstance(settings, dict):
            raise TypeError("it holds no JSON object")
        projects = settings.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise TypeError("its projects are no JSON object")
        project = projects.setdefault(str(directory), {})
        if not isinstance(project, dict):
            raise TypeError(f"its entry for {directory} is no JSON object")
        if project.get(TRUSTED):
            return
        project[TRUSTED] = True
        config.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.with_name(f"{config.name}.bytearmy.tmp")
        temporary.write_text(json.dumps(settings, indent=2) + "\n")
        if config.exists():
            os.chmod(temporary, config.stat().st_mode & 0o777)
        os.replace(temporary, config)
    except (OSError, ValueError, TypeError) as error:  # JSONDecodeError is a ValueError
        log.warning("Could not trust %s in %s: %s", directory, config, error)
