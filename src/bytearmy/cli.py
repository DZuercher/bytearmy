"""The `bytearmy` command in the panes. It lets the program of a pane, such as an agent,
steer the workspace of its own task; the orchestrator steers projects, tasks and the inbox
with it. Standard library only, so it starts fast."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The states an agent sets, by their commands: (state, status if none is given, help).
STATES = {
    "working": ("working", "Working", "you work on the task yourself, nothing blocks you (blue)"),
    "needs-input": ("needs_input", "Needs input", "the user has to do something (amber)"),
    "waiting": ("waiting", "Waiting", "you wait for an event outside bytearmy (red)"),
    "done": ("done", "Done", "the task is done, nothing more needs to be done (green)"),
    "merged": ("merged", "Merged", "its pull request is merged; projects with GitHub (purple)"),
}
REVIEW_POLL_SECONDS = 2.0


class Failed(Exception):
    """What went wrong, to be printed; the command then ends with an error."""


def call(method: str, path: str, body: object = None, prefix: str = "/agent") -> object:
    """Ask the core, as the pane this command runs in, or as the orchestrator."""
    url, key = os.environ.get("BYTEARMY_URL"), os.environ.get("BYTEARMY_TOKEN")
    if not url or not key:
        raise Failed("this command works only inside a pane of bytearmy")
    request = urllib.request.Request(
        f"{url}{prefix}{path}",
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
        notes += ["hidden"] * pane["hidden"]
        if pane["exit_code"] is not None:
            notes.append(f"ended with code {pane['exit_code']}")
        notes += ["you"] * (pane["id"] == own)
        lines.append(
            f"{pane['id']}  {pane['title']}" + (f"  ({', '.join(notes)})" if notes else "")
        )
    return "\n".join(lines) or "no panes"


def pane_open(args: argparse.Namespace) -> str:
    body = {
        "tool": args.tool,
        "args": args.values,
        "beside": args.beside,
        "hidden": args.hidden,
        "login": args.login,
    }
    opened = call("POST", "/panes", body)
    if opened["logged"]:
        return "added the entry to the task's log"
    if opened.get("exit_code") is not None:
        return ended_at_once(opened)
    return (
        f"opened {opened['pane']}" if opened["pane"] else f"started {args.tool} in its own window"
    )


def log(args: argparse.Namespace) -> str:
    """Short for `bytearmy pane open log entry=<text>`."""
    call("POST", "/panes", {"tool": "log", "args": [f"entry={args.entry}"]})
    return "added the entry to the task's log"


def ended_at_once(opened: dict) -> str:
    """What to say of a listener whose command ended right after it started: an error if it
    failed, as it then listens for nothing."""
    code, output = opened["exit_code"], opened.get("output", "")
    printed = f"\n\nThe last lines it printed:\n\n{output}" if output else ""
    if code != 0:
        raise Failed(
            f"the listener's command failed at once with exit code {code}, so nothing"
            " listens and no message follows. Fix the command, e.g. a missing permission or"
            f" login, and start the listener again.{printed}"
        )
    return (
        "the listener's command ended at once with exit code 0: the event has already"
        f" happened, and no message follows.{printed}"
    )


def listen(args: argparse.Namespace) -> str:
    """Short for `bytearmy pane open event_listener --hidden <event> <command>`."""
    body = {
        "tool": "event_listener",
        "args": [f"event={args.event}", f"command={args.command}"],
        "hidden": True,
    }
    opened = call("POST", "/panes", body)
    if opened.get("exit_code") is not None:
        return ended_at_once(opened)
    return (
        f"listening in hidden pane {opened['pane']}; you get a message when it ends."
        " Do not wait for it: go on with other work, or end your turn."
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


def pane_hide(args: argparse.Namespace) -> str:
    call("PUT", f"/panes/{args.id}/hidden", {"hidden": True})
    return f"hid {args.id}"


def pane_show(args: argparse.Namespace) -> str:
    call("PUT", f"/panes/{args.id}/hidden", {"hidden": False, "beside": args.beside})
    return f"showed {args.id}"


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
        "state": task["state"],
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
    return _change(status=args.status)


def task_set_state(args: argparse.Namespace) -> str:
    state, status, _help = STATES[args.action]
    return _change(state=state, status=args.status or status)


def task_set_description(args: argparse.Namespace) -> str:
    return _change(description=args.description)


def task_set_reminder(args: argparse.Namespace) -> str:
    return _change(reminder=None if args.date == "none" else args.date)


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


# -- commands of the orchestrator ----------------------------------------------


def above(method: str, path: str, body: object = None) -> object:
    return call(method, path, body, prefix="/orchestrator")


def _task_line(task: dict) -> str:
    notes = [task["status"]]
    if task["branch"]:
        notes.append(f"branch {task['branch']}")
    if task["master"]:
        notes.append(
            f"master {task['master']}" + ("" if task["master_running"] else " (not running)")
        )
    return f"  {task['slug']}  {task['name']}  ({', '.join(notes)})"


def _project_lines(project: dict) -> list[str]:
    head = f"{project['slug']}  {project['name']}"
    if project["github_url"]:
        head += f"  ({project['github_url']})"
    tasks = [_task_line(task) for task in [*project["backlog"], *project["tasks"]]]
    return [head, *(tasks or ["  no tasks"])]


def project_list(_args: argparse.Namespace) -> str:
    lines = [line for project in above("GET", "/projects") for line in _project_lines(project)]
    return "\n".join(lines) or "no projects"


def project_create(args: argparse.Namespace) -> str:
    body = {"name": args.name, "github_url": args.github_url, "description": args.description}
    created = above("POST", "/projects", body)
    return f"created project {created['slug']}"


def project_delete(args: argparse.Namespace) -> str:
    above("DELETE", f"/projects/{args.project}")
    return f"deleted project {args.project}"


def tasks_list(args: argparse.Namespace) -> str:
    projects = above("GET", "/projects")
    if args.project:
        projects = [project for project in projects if project["slug"] == args.project]
        if not projects:
            raise Failed(f"no project {args.project!r}")
    lines = [line for project in projects for line in _project_lines(project)]
    return "\n".join(lines) or "no projects"


def tasks_create(args: argparse.Namespace) -> str:
    body = {
        "name": args.name,
        "description": args.description,
        "branch": args.branch,
        "base": args.base,
        "dependencies": args.depends_on,
        "reminder": args.reminder,
    }
    created = above("POST", f"/projects/{args.project}/tasks", body)
    return f"created task {args.project}/{created['slug']} in the backlog"


def tasks_start(args: argparse.Namespace) -> str:
    if args.prompt is not None and args.prompt_file is not None:
        raise Failed("give --prompt or --prompt-file, not both")
    prompt = args.prompt if args.prompt_file is None else Path(args.prompt_file).read_text()
    started = above("POST", f"/projects/{args.project}/tasks/{args.task}/start", {"prompt": prompt})
    if started["master"]:
        return f"started {args.project}/{args.task}; its master is {started['master']}"
    return f"started {args.project}/{args.task}"


def tasks_delete(args: argparse.Namespace) -> str:
    above("DELETE", f"/projects/{args.project}/tasks/{args.task}")
    return f"deleted task {args.project}/{args.task}"


def inbox_list(args: argparse.Namespace) -> str:
    messages = [
        message for message in above("GET", "/inbox")["messages"] if not message["dismissed"]
    ]
    if not args.all:
        messages = [message for message in messages if message["unread"]]
    messages.sort(key=lambda message: not message["unread"])  # stable: newest first in each
    lines = []
    for message in messages:
        notes = [
            note for note in (message["kind"], message["reason"], message["updated_at"]) if note
        ]
        notes += ["unread"] * message["unread"]
        lines.append(
            f"{message['id']}  {message['title']}  [{message['context']}]  ({', '.join(notes)})"
        )
        if message["url"]:
            lines.append(f"  {message['url']}")
    return "\n".join(lines) or ("no messages" if args.all else "no unread messages")


def inbox_read(args: argparse.Namespace) -> str:
    above("POST", f"/inbox/{args.id}/read", {"unread": False})
    return f"marked {args.id} read"


def inbox_unread(args: argparse.Namespace) -> str:
    above("POST", f"/inbox/{args.id}/read", {"unread": True})
    return f"marked {args.id} unread"


def inbox_dismiss(args: argparse.Namespace) -> str:
    above("POST", f"/inbox/{args.id}/dismiss")
    return f"dismissed {args.id}"


def orchestrator_needs_input(_args: argparse.Namespace) -> str:
    above("PUT", "/needs-input", {"needs_input": True})
    return "the Orchestrator tab is amber until the user types into it"


def orchestrator_working(_args: argparse.Namespace) -> str:
    above("PUT", "/needs-input", {"needs_input": False})
    return "the Orchestrator tab is no longer amber"


def review(args: argparse.Namespace) -> str:
    """Ask the user to review the changes; waits until they finish, then prints the review."""
    mode = "uncommitted" if args.uncommitted else "branch"
    opened = call("POST", "/reviews", {"mode": mode, "note": args.note or ""})
    print(f"opened the review in pane {opened['pane']}; waiting for the user", flush=True)
    while True:
        state = call("GET", f"/reviews/{opened['pane']}")
        if state["done"]:
            return state["text"].rstrip("\n")
        time.sleep(REVIEW_POLL_SECONDS)


def answer_draft(args: argparse.Namespace) -> str:
    """Show the conversation with the draft to the user; the decision comes as a message."""
    if (args.body is None) == (args.body_file is None):
        raise Failed("give the draft as --body or --body-file, not both")
    body = args.body if args.body is not None else Path(args.body_file).read_text()
    opened = call("POST", "/answers", {"url": args.url, "body": body})
    return (
        f"opened the draft answer {opened['pane']} for the user. When they approve or discard"
        ' it, you get a message that starts with "Draft answer". Do not wait for it: go on'
        " with other work, or end your turn."
    )


def answer_send(args: argparse.Namespace) -> str:
    return call("POST", f"/answers/{args.id}/send")["text"]


def pr_create(args: argparse.Namespace) -> str:
    body = Path(args.body_file).read_text() if args.body_file else args.body
    request = {"title": args.title, "body": body, "base": args.base, "draft": args.draft}
    return call("POST", "/pulls", request)["text"]


def pr_view(_args: argparse.Namespace) -> str:
    return call("GET", "/pulls")["text"]


def pr_merge(args: argparse.Namespace) -> str:
    request = {"method": args.method, "delete_branch": args.delete_branch}
    return call("POST", "/pulls/merge", request)["text"]


def merge_start(args: argparse.Namespace) -> str:
    return call("POST", "/merges", {"branch": args.branch or ""})["text"]


def merge_continue(_args: argparse.Namespace) -> str:
    return call("POST", "/merges/continue")["text"]


def merge_abort(_args: argparse.Namespace) -> str:
    return call("POST", "/merges/abort")["text"]


def workflow_list(_args: argparse.Namespace) -> str:
    return call("GET", "/workflows")["text"]


def _inputs(args: argparse.Namespace) -> dict[str, str]:
    """The workflow's inputs: those of --inputs-file, then each --field over them."""
    inputs: dict[str, str] = {}
    if args.inputs_file:
        try:
            given = json.loads(Path(args.inputs_file).read_text())
        except ValueError as error:
            raise Failed(f"{args.inputs_file} holds no valid JSON: {error}") from None
        if not isinstance(given, dict):
            raise Failed(f"{args.inputs_file} must hold a JSON object of inputs by name")
        for name, value in given.items():
            if isinstance(value, (dict, list)) or value is None:
                raise Failed(f"input {name!r} must be a string, number or boolean")
            inputs[name] = json.dumps(value) if isinstance(value, bool) else str(value)
    for field in args.field:
        name, equals, value = field.partition("=")
        if not equals or not name:
            raise Failed(f"--field {field!r} is not of the form name=value")
        inputs[name] = value
    return inputs


def workflow_run(args: argparse.Namespace) -> str:
    request = {"workflow": args.workflow, "ref": args.ref, "inputs": _inputs(args)}
    return call("POST", "/workflows/run", request)["text"]


def workflow_runs(args: argparse.Namespace) -> str:
    query = urllib.parse.urlencode(
        {"workflow": args.workflow or "", "branch": args.branch, "limit": args.limit}
    )
    return call("GET", f"/workflows/runs?{query}")["text"]


# -- command line --------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(
        prog="bytearmy", description="Steer the workspace of this pane's task in bytearmy."
    )
    groups = main.add_subparsers(
        dest="group",
        required=True,
        metavar="{pane,tool,task,agent,log,listen,review,answer,pr,merge,workflow,project,tasks,inbox}",
    )

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
    opening.add_argument(
        "--hidden", action="store_true", help="open it hidden: listed in the tasks bar only"
    )
    opening.add_argument(
        "--login",
        action="store_true",
        help="a page to log in: the pane closes once the page comes back to this machine",
    )
    command(pane, "close", pane_close, "close a pane").add_argument("id")
    command(pane, "maximize", pane_maximize, "let a pane fill the workspace").add_argument("id")
    command(pane, "restore", pane_restore, "show every pane again after maximize")
    command(
        pane, "hide", pane_hide, "take a pane off the workspace; it keeps running"
    ).add_argument("id")
    showing = command(pane, "show", pane_show, "put a hidden pane back on the workspace")
    showing.add_argument("id")
    showing.add_argument("--beside", help="the pane to place it next to")
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
    for name, (_state, default, help) in STATES.items():
        stating = command(task, name, task_set_state, f"set the state: {help}")
        stating.add_argument("status", nargs="?", help=f'the status tag; "{default}" if none')
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

    logging = groups.add_parser(
        "log", help="add an entry to the task's log", description="add an entry to the task's log"
    )
    logging.set_defaults(run=log)
    logging.add_argument("entry", help="the text of the entry; the time is taken from the clock")

    listening = groups.add_parser(
        "listen",
        help="wait for an event in a hidden pane; you get a message when it happens",
        description="Run a command that ends when an event happens, e.g. `gh pr checks 12 "
        "--watch`, in a hidden event_listener pane. When it ends, bytearmy sends you a message "
        "with its exit code and the last lines it printed.",
    )
    listening.set_defaults(run=listen)
    listening.add_argument("event", help="what it waits for, e.g. 'CI of PR #12'")
    listening.add_argument("command", help="a shell command that ends when the event happens")

    # The orchestrator's commands; the core refuses them to the panes.
    project = groups.add_parser("project", help="the projects (orchestrator only)").add_subparsers(
        dest="action", required=True
    )
    command(project, "list", project_list, "list the projects with their tasks")
    creating = command(project, "create", project_create, "create a project")
    creating.add_argument("name")
    creating.add_argument("--github-url", default="", help="its tasks clone this repository")
    creating.add_argument("--description", default="")
    deleting = command(project, "delete", project_delete, "delete a project and all its files")
    deleting.add_argument("project", help="the project's slug")

    tasks = groups.add_parser(
        "tasks", help="the tasks of every project (orchestrator only)"
    ).add_subparsers(dest="action", required=True)
    command(tasks, "list", tasks_list, "list the tasks, backlog first").add_argument(
        "project", nargs="?", help="only this project"
    )
    creating = command(tasks, "create", tasks_create, "create a task in a project's backlog")
    creating.add_argument("project", help="the project's slug")
    creating.add_argument("name")
    creating.add_argument("--description", default="")
    creating.add_argument("--branch", default="", help="checked out in the task's clone")
    creating.add_argument(
        "--base", default="", help="the branch a new --branch starts from; default: the default"
    )
    creating.add_argument(
        "--depends-on", nargs="*", default=[], metavar="TASK", help="tasks it waits for"
    )
    creating.add_argument("--reminder", help="YYYY-MM-DD")
    starting = command(
        tasks, "start", tasks_start, "move a task into the task bar, optionally with a master"
    )
    starting.add_argument("project", help="the project's slug")
    starting.add_argument("task", help="the task's slug")
    starting.add_argument("--prompt", help="start the task's master with this first message")
    starting.add_argument("--prompt-file", help="read the master's prompt from this file")
    deleting = command(tasks, "delete", tasks_delete, "end a task's programs and delete it")
    deleting.add_argument("project", help="the project's slug")
    deleting.add_argument("task", help="the task's slug")

    inbox = groups.add_parser("inbox", help="the user's inbox (orchestrator only)").add_subparsers(
        dest="action", required=True
    )
    command(inbox, "list", inbox_list, "list the unread messages").add_argument(
        "--all", action="store_true", help="also the read ones"
    )
    command(inbox, "read", inbox_read, "mark a message read").add_argument("id")
    command(inbox, "unread", inbox_unread, "mark a message unread").add_argument("id")
    command(inbox, "dismiss", inbox_dismiss, "dismiss a message").add_argument("id")

    orchestrating = groups.add_parser(
        "orchestrator", help="tell the user that you wait for them (orchestrator only)"
    ).add_subparsers(dest="action", required=True)
    command(
        orchestrating,
        "needs-input",
        orchestrator_needs_input,
        "turn the Orchestrator tab amber: you wait for the user; it ends when they type",
    )
    command(orchestrating, "working", orchestrator_working, "end the amber of the tab yourself")

    reviewing = groups.add_parser(
        "review",
        help="ask the user to review the changes; prints their comments when they finish",
        description="Open the changes of the task's clone for the user to review, wait until "
        "they finish, and print their comments.",
    )
    reviewing.set_defaults(run=review)
    reviewing.add_argument(
        "--uncommitted",
        action="store_true",
        help="show only what changed since the last commit, not all since the default branch",
    )
    reviewing.add_argument("--note", help="what the user should look at")

    answering = groups.add_parser(
        "answer", help="answer a comment on GitHub, once the user approves your draft"
    ).add_subparsers(dest="action", required=True)
    drafting = command(
        answering,
        "draft",
        answer_draft,
        "show the user a GitHub conversation with your draft answer below it, to edit and "
        "approve or discard; you get a message when they decide",
    )
    drafting.add_argument(
        "url", help="the comment to answer, e.g. https://github.com/o/r/pull/12#discussion_r34"
    )
    drafting.add_argument("--body", help="the draft answer, in GitHub Markdown")
    drafting.add_argument("--body-file", help="read the draft answer from this file")
    command(
        answering, "send", answer_send, "post an answer the user approved; sets the task waiting"
    ).add_argument("id", help="the draft's id, as the approval message names it")

    pulls = groups.add_parser(
        "pr", help="the pull request of the task's branch, with gh"
    ).add_subparsers(dest="action", required=True)
    creating = command(
        pulls, "create", pr_create, "push the task's branch and open its pull request"
    )
    creating.add_argument("--title", required=True)
    creating.add_argument("--body", default="")
    creating.add_argument("--body-file", help="read the body from this file")
    creating.add_argument(
        "--base", default="", help="the branch to merge into; default: the default branch"
    )
    creating.add_argument("--draft", action="store_true")
    command(pulls, "view", pr_view, "show the pull request of the task's branch")
    merging = command(pulls, "merge", pr_merge, "merge the pull request of the task's branch")
    merging.add_argument("--method", choices=("merge", "squash", "rebase"), default="squash")
    merging.add_argument(
        "--delete-branch", action="store_true", help="delete the branch on GitHub afterwards"
    )

    merges = groups.add_parser(
        "merge", help="merge a branch of origin into the task's branch, with git"
    ).add_subparsers(dest="action", required=True)
    starting = command(
        merges,
        "start",
        merge_start,
        "fetch a branch of origin and merge it; a clean merge is committed, conflicts are "
        "listed and left to resolve",
    )
    starting.add_argument(
        "branch",
        nargs="?",
        help="the branch of origin to merge; default: the task's base branch, or the "
        "default branch if the task has none",
    )
    command(merges, "continue", merge_continue, "commit the merge once its conflicts are resolved")
    command(merges, "abort", merge_abort, "undo the merge in progress")

    workflows = groups.add_parser(
        "workflow", help="the GitHub Actions workflows of the task's repository, with gh"
    ).add_subparsers(dest="action", required=True)
    command(workflows, "list", workflow_list, "list the workflows of the repository")
    running = command(
        workflows,
        "run",
        workflow_run,
        "dispatch a workflow (it needs a workflow_dispatch trigger) with inputs",
    )
    running.add_argument("workflow", help="its name, ID or file name, e.g. deploy.yml")
    running.add_argument(
        "--ref",
        default="",
        help="the branch or tag to run it on; default: the task's branch, or the default "
        "branch if the task has none. The ref must be pushed.",
    )
    running.add_argument(
        "-f",
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="an input of the workflow; give it once per input",
    )
    running.add_argument(
        "--inputs-file", help="read the inputs from a JSON object in this file; --field wins"
    )
    listing = command(workflows, "runs", workflow_runs, "list the latest workflow runs")
    listing.add_argument("workflow", nargs="?", help="only the runs of this workflow")
    listing.add_argument("--branch", default="", help="only the runs on this branch")
    listing.add_argument("--limit", type=int, default=10, help="how many; default 10")
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
