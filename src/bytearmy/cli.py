"""The `bytearmy` command in the panes. It lets the program of a pane, such as an agent,
steer the workspace of its own task. Standard library only, so it starts fast."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

COLORS = ("red", "amber", "green", "blue", "purple", "none")


class Failed(Exception):
    """What went wrong, to be printed; the command then ends with an error."""


def call(method: str, path: str, body: object = None) -> object:
    """Ask the core, as the pane this command runs in."""
    url, key = os.environ.get("BYTEARMY_URL"), os.environ.get("BYTEARMY_TOKEN")
    if not url or not key:
        raise Failed("this command works only inside a pane of bytearmy")
    request = urllib.request.Request(
        f"{url}/agent{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    # No proxy: the core runs on this machine, whatever the environment says.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            answer = response.read()
    except urllib.error.HTTPError as error:
        raise Failed(_detail(error.read()) or f"bytearmy answered {error.code}") from None
    except urllib.error.URLError as error:
        raise Failed(f"cannot reach bytearmy at {url}: {error.reason}") from None
    return json.loads(answer) if answer else None


def _detail(answer: bytes) -> str:
    """The reason in an error answer; for invalid input, what was wrong with it."""
    try:
        detail = json.loads(answer)["detail"]
    except (ValueError, KeyError, TypeError):
        return answer.decode(errors="replace")
    if isinstance(detail, list):
        return "; ".join(
            str(problem.get("msg", problem)).removeprefix("Value error, ") for problem in detail
        )
    return str(detail)


# -- commands ----------------------------------------------------------------


def pane_list(_args: argparse.Namespace) -> str:
    workspace = call("GET", "/panes")
    own = os.environ.get("BYTEARMY_PANE")
    lines = []
    for pane in workspace["panes"].values():
        notes = [f"agent {pane['agent']}"] if pane["agent"] else []
        notes += ["master"] * (pane["id"] == workspace["master"])
        notes += ["maximized"] * (pane["id"] == workspace["maximized"])
        notes += ["highlighted"] * pane["highlighted"]
        if pane["exit_code"] is not None:
            notes.append(f"ended with code {pane['exit_code']}")
        notes += ["you"] * (pane["id"] == own)
        lines.append(
            f"{pane['id']}  {pane['title']}" + (f"  ({', '.join(notes)})" if notes else "")
        )
    return "\n".join(lines) or "no panes"


def pane_open(args: argparse.Namespace) -> str:
    body = {"tool": args.tool, "args": args.values, "beside": args.beside}
    opened = call("POST", "/panes", body)
    return (
        f"opened {opened['pane']}" if opened["pane"] else f"started {args.tool} in its own window"
    )


def pane_close(args: argparse.Namespace) -> str:
    call("DELETE", f"/panes/{args.id}")
    return f"closed {args.id}"


def pane_maximize(args: argparse.Namespace) -> str:
    call("PUT", "/maximized", {"slug": args.id})
    return f"maximized {args.id}"


def pane_restore(_args: argparse.Namespace) -> str:
    call("PUT", "/maximized", {"slug": None})
    return "every pane is shown again"


def pane_highlight(args: argparse.Namespace) -> str:
    call("PUT", f"/panes/{args.id}/highlight", {"on": True})
    return f"highlighted {args.id}"


def pane_unhighlight(args: argparse.Namespace) -> str:
    call("PUT", f"/panes/{args.id}/highlight", {"on": False})
    return f"{args.id} is no longer highlighted"


def pane_move(args: argparse.Namespace) -> str:
    call("PUT", f"/panes/{args.id}/move", {"beside": args.other})
    return f"moved {args.id} next to {args.other}"


def tool_list(_args: argparse.Namespace) -> str:
    lines = []
    for tool in call("GET", "/tools"):
        values = [
            f"{parameter['name']}"
            + (f" (default {parameter['default']})" if parameter["default"] else "")
            for parameter in tool["parameters"]
        ]
        takes = f"  takes: {', '.join(values)}" if values else ""
        lines.append(f"{tool['id']}  {tool['name']}, {tool['kind']}{takes}")
    return "\n".join(lines)


def task_show(_args: argparse.Namespace) -> str:
    return _describe(call("GET", "/task"))


def _describe(task: dict) -> str:
    rows = {
        "name": task["name"],
        "status": task["status"],
        "color": task["color"] or "none",
        "description": task["description"],
        "branch": task["branch"],
        "reminder": task["reminder"] or "none",
        "directory": task["path"],
        "repository": str(Path(task["path"]) / task["repo"]) if task["clone"] == "ready" else "",
    }
    return "\n".join(f"{name}: {value}" for name, value in rows.items() if value)


def _change(**fields: object) -> str:
    return _describe(call("PATCH", "/task", fields))


def task_set_status(args: argparse.Namespace) -> str:
    if args.color is None:
        return _change(status=args.status)
    return _change(status=args.status, color=_color(args.color))


def task_set_color(args: argparse.Namespace) -> str:
    return _change(color=_color(args.color))


def task_set_description(args: argparse.Namespace) -> str:
    return _change(description=args.description)


def task_set_reminder(args: argparse.Namespace) -> str:
    return _change(reminder=None if args.date == "none" else args.date)


def _color(name: str) -> str:
    return "" if name == "none" else name


def agent_list(_args: argparse.Namespace) -> str:
    own = os.environ.get("BYTEARMY_PANE")
    lines = []
    for agent in call("GET", "/agents"):
        notes = ["master"] * agent["master"] + ["not running"] * (not agent["running"])
        notes += ["you"] * (agent["pane"] == own)
        lines.append(
            f"{agent['name']}  pane {agent['pane']}" + (f"  ({', '.join(notes)})" if notes else "")
        )
    return "\n".join(lines) or "no agents"


def agent_spawn(args: argparse.Namespace) -> str:
    if (args.prompt is None) == (args.prompt_file is None):
        raise Failed("give the prompt, or --prompt-file, but not both")
    prompt = args.prompt if args.prompt is not None else Path(args.prompt_file).read_text()
    body = {"role": args.role, "prompt": prompt, "beside": args.beside}
    spawned = call("POST", "/agents", body)
    return f"spawned {spawned['agent']} in pane {spawned['pane']}"


# -- command line --------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(
        prog="bytearmy", description="Steer the workspace of this pane's task in bytearmy."
    )
    groups = main.add_subparsers(dest="group", required=True, metavar="{pane,tool,task,agent}")

    def command(group, name: str, run, help: str):
        sub = group.add_parser(name, help=help, description=help)
        sub.set_defaults(run=run)
        return sub

    pane = groups.add_parser("pane", help="the panes of the task").add_subparsers(
        dest="action", required=True
    )
    command(pane, "list", pane_list, "list the panes with their ids")
    opening = command(pane, "open", pane_open, "open a tool in a new pane next to this one")
    opening.add_argument("tool", help="see `bytearmy tool list`")
    opening.add_argument("values", nargs="*", help="the tool's values, in order or as name=value")
    opening.add_argument("--beside", help="the pane to open it next to")
    command(pane, "close", pane_close, "close a pane").add_argument("id")
    command(pane, "maximize", pane_maximize, "let a pane fill the workspace").add_argument("id")
    command(pane, "restore", pane_restore, "show every pane again after maximize")
    command(pane, "highlight", pane_highlight, "draw the user's eye to a pane").add_argument("id")
    command(pane, "unhighlight", pane_unhighlight, "end a highlight").add_argument("id")
    moving = command(pane, "move", pane_move, "place a pane next to another one")
    moving.add_argument("id")
    moving.add_argument("other")

    tool = groups.add_parser("tool", help="the tools a pane can show").add_subparsers(
        dest="action", required=True
    )
    command(tool, "list", tool_list, "list the tools with the values they take")

    task = groups.add_parser("task", help="the task of this pane").add_subparsers(
        dest="action", required=True
    )
    command(task, "show", task_show, "show the task's data")
    status = command(task, "set-status", task_set_status, "set the status tag in the task bar")
    status.add_argument("status")
    status.add_argument("--color", choices=COLORS, help="also color the task in the task bar")
    coloring = command(task, "set-color", task_set_color, "color the task in the task bar")
    coloring.add_argument("color", choices=COLORS)
    describing = command(task, "set-description", task_set_description, "set the description")
    describing.add_argument("description")
    reminding = command(task, "set-reminder", task_set_reminder, "set or remove the reminder")
    reminding.add_argument("date", help="YYYY-MM-DD, or none")

    agent = groups.add_parser("agent", help="the agents of the task").add_subparsers(
        dest="action", required=True
    )
    command(agent, "list", agent_list, "list the agents with their names and panes")
    spawning = command(agent, "spawn", agent_spawn, "start an agent with a prompt in a new pane")
    spawning.add_argument("role", help="part of its name, e.g. reviewer")
    spawning.add_argument("prompt", nargs="?", help="what the new agent should do")
    spawning.add_argument("--prompt-file", help="read the prompt from this file")
    spawning.add_argument("--beside", help="the pane to open it next to")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(args.run(args))
    except Failed as failure:
        print(f"bytearmy: {failure}", file=sys.stderr)
        return 1
    except OSError as error:  # e.g. a prompt file that cannot be read
        print(f"bytearmy: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
