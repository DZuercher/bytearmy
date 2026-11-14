"""Throwaway spike CLI: lets a process inside a pane steer the bytearmy core.

Standard library only, so the shim on PATH starts fast and needs no environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

TOOLS = ("terminal", "claude", "webpage", "ide")


def call(method: str, **params) -> object:
    try:
        url, token = os.environ["BYTEARMY_URL"], os.environ["BYTEARMY_TOKEN"]
    except KeyError as missing:
        sys.exit(f"bytearmy: {missing} is not set; run this inside a bytearmy pane")
    request = urllib.request.Request(
        f"{url}/rpc",
        data=json.dumps({"method": method, "params": params}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)["result"]
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        sys.exit(f"bytearmy: {method} failed with HTTP {error.code}: {detail}")
    except urllib.error.URLError as error:
        sys.exit(f"bytearmy: cannot reach the core at {url}: {error.reason}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bytearmy", description="Steer the bytearmy app.")
    groups = parser.add_subparsers(dest="group", required=True)

    pane = groups.add_parser("pane", help="open, close and arrange panes")
    pane_actions = pane.add_subparsers(dest="action", required=True)
    pane_open = pane_actions.add_parser("open", help="open a tool in a new pane")
    pane_open.add_argument("tool", choices=TOOLS)
    pane_open.add_argument("arg", nargs="?", help="tool argument, e.g. the URL for webpage")
    pane_close = pane_actions.add_parser("close", help="close a pane")
    pane_close.add_argument("id")
    pane_maximize = pane_actions.add_parser("maximize", help="let one pane fill the workspace")
    pane_maximize.add_argument("id")
    pane_actions.add_parser("restore", help="undo maximize")
    pane_actions.add_parser("list", help="list panes")

    task = groups.add_parser("task", help="change the task's metadata")
    task_actions = task.add_subparsers(dest="action", required=True)
    set_status = task_actions.add_parser("set-status", help="set the status tag")
    set_status.add_argument("status")
    set_status.add_argument("--color")

    args = parser.parse_args(argv)
    match (args.group, args.action):
        case ("pane", "open"):
            result = call("pane.open", tool=args.tool, arg=args.arg)
        case ("pane", "close"):
            result = call("pane.close", id=args.id)
        case ("pane", "maximize"):
            result = call("pane.maximize", id=args.id)
        case ("pane", "restore"):
            result = call("pane.restore")
        case ("pane", "list"):
            result = call("pane.list")
        case ("task", "set-status"):
            result = call("task.set_status", status=args.status, color=args.color)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
