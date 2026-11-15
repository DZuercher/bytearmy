"""Agents: Claude sessions in panes. Each has a name, each task one master, and every agent
is told how to steer bytearmy and who else works on its task."""

from __future__ import annotations

from pathlib import Path

from bytearmy.models import Pane, Task
from bytearmy.store import METADATA_DIR

MASTER = "master"  # the role of the first agent of a task
INSTRUCTIONS_FILE = Path(".claude") / "bytearmy.md"  # below the root; the user may change it
# Fingerprints of the texts earlier versions wrote; a file that still has one is replaced.
EARLIER_INSTRUCTIONS = {
    "d7b7e2ff9de55b90644b86a495241721af6ed7405a674dfa89d702b49bee815f",
    "75400f6d42471783dd12939ab458350e54dd7a6809b4112f5b36cb9562a8c3ec",
}

INSTRUCTIONS = """\
# bytearmy

You run in a pane of bytearmy, a desktop app in which the user follows several agents that
work on tasks side by side. Every task has a workspace of panes: terminals, Claude sessions
like you, web pages and an editor. The `bytearmy` command on your PATH steers the workspace
of your own task; it cannot reach other tasks.

## Panes

- `bytearmy pane list`: the panes of your task, with their ids.
- `bytearmy tool list`: the tools a pane can show, with the values they take.
- `bytearmy pane open <tool> [value ...]`: open a tool in a new pane next to yours, e.g.
  `bytearmy pane open webpage https://github.com/owner/repo` or
  `bytearmy pane open vim notes.md`. Files are given relative to the task directory.
- To show the user a result, write an HTML file into the task directory and open it with
  `bytearmy pane open html <file>`. The pane shows every change of the file at once.
- `bytearmy pane close <id>`, `bytearmy pane maximize <id>`, `bytearmy pane restore`.
- `bytearmy pane move <id> <other>`: place a pane next to another one.
- `bytearmy pane highlight <id>`: draw the user's eye to a pane; it ends when the user
  clicks into the pane, or with `bytearmy pane unhighlight <id>`.

## The task

- `bytearmy task show`: name, status, description and the other data of your task.
- `bytearmy task set-status "<status>" [--color amber]`: the status tag in the task bar,
  e.g. "Needs review". The color (red, amber, green, blue, purple or none) colors the task
  in the task bar; use it when the user should look at the task.
- `bytearmy task set-color <color>`, `bytearmy task set-description "<text>"`,
  `bytearmy task set-reminder <YYYY-MM-DD|none>`.
- `bytearmy log "<text>"`: add an entry to the task's log; the time is taken from the clock
  and you are named as its author. The user writes to the same log, and
  `bytearmy pane open show-log` shows it.

## Other agents

- `bytearmy agent list`: the agents of your task, with their names and panes.
- `bytearmy agent spawn <role> "<prompt>"`: start a new agent in a pane of its own, in the
  same directory as you, with the prompt as its first message. Its name is
  `<project>-<task>-<role>`. Give it everything it needs to know in the prompt, and tell it
  what to report back.
- Once an agent you spawned has reported back and you need nothing more from it, close its
  pane with `bytearmy pane close <pane>`. Its pane is in `bytearmy agent list`.
- Talk to the agents of your task with the SendMessage tool, by their names. Do not message
  sessions that `bytearmy agent list` does not name: they belong to other work.
"""

ABOUT_YOU = """
## You

Your name is `{name}`, your pane is {pane}, your task is "{task}".
{relation}
"""

IS_MASTER = (
    "You are the master agent of this task: you plan the work, spawn agents for parts of it,\n"
    "and keep the user informed with the status and color of the task."
)
SPAWNED = (
    "`{spawner}` spawned you with the prompt you got as your first message. When you are\n"
    "done, or stuck, tell it with SendMessage. It closes your pane when it needs nothing\n"
    "more from you."
)
HAS_MASTER = "The master agent of this task is `{master}`."
NO_MASTER = "The task has no master agent right now."


def agent_name(project: str, task: str, role: str) -> str:
    """The session name of an agent; it is unique on the machine, not only in the task."""
    return f"{project}-{task}-{role}"


def next_role(taken: set[str]) -> str:
    """The role of an agent the user opens while the task has a master: agent-2, agent-3, ..."""
    number = 2
    while f"agent-{number}" in taken:
        number += 1
    return f"agent-{number}"


def write_instructions(root: Path, task: Task, pane: Pane) -> Path:
    """The instructions an agent starts with: the root's file, then who it is.

    Written anew at every start, so a restarted agent learns who the master is now.
    """
    master = task.workspace.panes.get(task.workspace.master or "")
    if task.workspace.master == pane.id:
        relation = IS_MASTER
    elif pane.spawned_by:
        relation = SPAWNED.format(spawner=pane.spawned_by)
        if master is not None and master.agent != pane.spawned_by:
            relation += "\n" + HAS_MASTER.format(master=master.agent)
    elif master is not None:
        relation = HAS_MASTER.format(master=master.agent)
    else:
        relation = NO_MASTER
    general = root / INSTRUCTIONS_FILE
    text = general.read_text() if general.is_file() else INSTRUCTIONS
    file = Path(task.path) / METADATA_DIR / "agents" / f"{pane.id}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        text + ABOUT_YOU.format(name=pane.agent, pane=pane.id, task=task.name, relation=relation)
    )
    return file
