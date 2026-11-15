"""The colors of the window. Each color scheme is one TOML file in `<root>/.bytearmy/themes`;
`<root>/.bytearmy/config.toml` names the one in use, and without it light is used."""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from pydantic import ValidationError

from bytearmy.models import Theme
from bytearmy.store import METADATA_DIR
from bytearmy.tools import write_premade

log = logging.getLogger(__name__)

THEMES_DIR = Path(METADATA_DIR) / "themes"
CONFIG_FILE = Path(METADATA_DIR) / "config.toml"
DEFAULT = "light"
NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

HEADER = """\
# A bytearmy color scheme. The file name is its name; it is used when
# <root>/.bytearmy/config.toml holds `color_scheme = "<name>"`.
#   appearance  "light" or "dark": the look of scroll bars, form fields and the like
#   [colors]    any CSS color. A color that is missing is taken from the light scheme.
#               shell: the bars around the workspace, hover: an item under the mouse,
#               primary-hover: a button on a primary background under the mouse,
#               task-<color>: the background of a task an agent colored,
#               task-<color>-mark: its left border
"""

# The colors the window had before it had schemes.
LIGHT = {
    "background": "oklch(1 0 0)",
    "foreground": "oklch(0.21 0.006 285.885)",
    "card": "oklch(1 0 0)",
    "card-foreground": "oklch(0.145 0 0)",
    "popover": "oklch(1 0 0)",
    "popover-foreground": "oklch(0.145 0 0)",
    "primary": "oklch(0.208 0.042 265.755)",
    "primary-foreground": "oklch(0.985 0 0)",
    "secondary": "oklch(0.97 0 0)",
    "secondary-foreground": "oklch(0.205 0 0)",
    "muted": "oklch(0.97 0 0)",
    "muted-foreground": "oklch(0.442 0.017 285.786)",
    "accent": "oklch(0.97 0 0)",
    "accent-foreground": "oklch(0.205 0 0)",
    "destructive": "oklch(0.577 0.245 27.325)",
    "border": "oklch(0.92 0.004 286.32)",
    "input": "oklch(0.92 0.004 286.32)",
    "ring": "oklch(0.708 0 0)",
    "chart-1": "oklch(0.87 0 0)",
    "chart-2": "oklch(0.556 0 0)",
    "chart-3": "oklch(0.439 0 0)",
    "chart-4": "oklch(0.371 0 0)",
    "chart-5": "oklch(0.269 0 0)",
    "sidebar": "oklch(0.985 0 0)",
    "sidebar-foreground": "oklch(0.145 0 0)",
    "sidebar-primary": "oklch(0.205 0 0)",
    "sidebar-primary-foreground": "oklch(0.985 0 0)",
    "sidebar-accent": "oklch(0.97 0 0)",
    "sidebar-accent-foreground": "oklch(0.205 0 0)",
    "sidebar-border": "oklch(0.922 0 0)",
    "sidebar-ring": "oklch(0.708 0 0)",
    "shell": "oklch(0.985 0 0)",
    "hover": "oklch(0.967 0.001 286.375)",
    "primary-hover": "oklch(1 0 0 / 20%)",
    "task-red": "oklch(0.971 0.013 17.38)",
    "task-red-mark": "oklch(0.637 0.237 25.331)",
    "task-amber": "oklch(0.987 0.022 95.277)",
    "task-amber-mark": "oklch(0.769 0.188 70.08)",
    "task-green": "oklch(0.982 0.018 155.826)",
    "task-green-mark": "oklch(0.723 0.219 149.579)",
    "task-blue": "oklch(0.97 0.014 254.604)",
    "task-blue-mark": "oklch(0.623 0.214 259.815)",
    "task-purple": "oklch(0.977 0.014 308.299)",
    "task-purple-mark": "oklch(0.627 0.265 303.9)",
}

DARK = {
    "background": "oklch(0.145 0 0)",
    "foreground": "oklch(0.985 0 0)",
    "card": "oklch(0.205 0 0)",
    "card-foreground": "oklch(0.985 0 0)",
    "popover": "oklch(0.205 0 0)",
    "popover-foreground": "oklch(0.985 0 0)",
    "primary": "oklch(0.922 0 0)",
    "primary-foreground": "oklch(0.205 0 0)",
    "secondary": "oklch(0.269 0 0)",
    "secondary-foreground": "oklch(0.985 0 0)",
    "muted": "oklch(0.269 0 0)",
    "muted-foreground": "oklch(0.708 0 0)",
    "accent": "oklch(0.269 0 0)",
    "accent-foreground": "oklch(0.985 0 0)",
    "destructive": "oklch(0.704 0.191 22.216)",
    "border": "oklch(1 0 0 / 10%)",
    "input": "oklch(1 0 0 / 15%)",
    "ring": "oklch(0.556 0 0)",
    "chart-1": "oklch(0.87 0 0)",
    "chart-2": "oklch(0.556 0 0)",
    "chart-3": "oklch(0.439 0 0)",
    "chart-4": "oklch(0.371 0 0)",
    "chart-5": "oklch(0.269 0 0)",
    "sidebar": "oklch(0.205 0 0)",
    "sidebar-foreground": "oklch(0.985 0 0)",
    "sidebar-primary": "oklch(0.488 0.243 264.376)",
    "sidebar-primary-foreground": "oklch(0.985 0 0)",
    "sidebar-accent": "oklch(0.269 0 0)",
    "sidebar-accent-foreground": "oklch(0.985 0 0)",
    "sidebar-border": "oklch(1 0 0 / 10%)",
    "sidebar-ring": "oklch(0.556 0 0)",
    "shell": "oklch(0.205 0 0)",
    "hover": "oklch(0.269 0 0)",
    "primary-hover": "oklch(0 0 0 / 10%)",
    "task-red": "oklch(0.258 0.092 26.042)",
    "task-red-mark": "oklch(0.704 0.191 22.216)",
    "task-amber": "oklch(0.279 0.077 45.635)",
    "task-amber-mark": "oklch(0.828 0.189 84.429)",
    "task-green": "oklch(0.266 0.065 152.934)",
    "task-green-mark": "oklch(0.792 0.209 151.711)",
    "task-blue": "oklch(0.282 0.091 267.935)",
    "task-blue-mark": "oklch(0.707 0.165 254.624)",
    "task-purple": "oklch(0.291 0.149 302.717)",
    "task-purple-mark": "oklch(0.714 0.203 305.504)",
}


def premade_text(appearance: str, colors: dict[str, str]) -> str:
    lines = "".join(f'{name} = "{value}"\n' for name, value in colors.items())
    return f'{HEADER}\nappearance = "{appearance}"\n\n[colors]\n{lines}'


PREMADE = {"light": premade_text("light", LIGHT), "dark": premade_text("dark", DARK)}


def ensure_premade(root: Path) -> None:
    """Write the color schemes bytearmy comes with, unless the user has changed their files."""
    for name, text in PREMADE.items():
        write_premade(root / THEMES_DIR / f"{name}.toml", text, set())


def chosen(root: Path) -> str:
    """The name of the scheme in use; light if the config names none or cannot be read."""
    file = root / CONFIG_FILE
    if not file.exists():
        return DEFAULT
    try:
        name = tomllib.loads(file.read_text()).get("color_scheme", DEFAULT)
    except (OSError, tomllib.TOMLDecodeError) as error:
        log.warning("Using the light color scheme, %s cannot be read: %s", CONFIG_FILE, error)
        return DEFAULT
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        log.warning("Using the light color scheme, %r is no scheme name", name)
        return DEFAULT
    return name


def load_theme(root: Path) -> Theme:
    """The scheme in use; light if its file cannot be read as one."""
    name = chosen(root)
    try:
        theme = Theme(**tomllib.loads((root / THEMES_DIR / f"{name}.toml").read_text()), name=name)
    except (OSError, tomllib.TOMLDecodeError, ValidationError, TypeError) as error:
        log.warning("Using the light color scheme, %s cannot be read: %s", name, error)
        return Theme(colors=dict(LIGHT))
    theme.colors = {**LIGHT, **theme.colors}
    return theme


def stamp(root: Path) -> str:
    """A text that changes when the config or the scheme in use changes."""
    files = [root / CONFIG_FILE, root / THEMES_DIR / f"{chosen(root)}.toml"]
    stamps = []
    for file in files:
        try:
            status = file.stat()
            stamps.append(f"{status.st_mtime_ns}-{status.st_size}")
        except OSError:
            stamps.append("missing")
    return " ".join(stamps)
