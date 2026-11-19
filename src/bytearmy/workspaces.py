"""The workspaces of the started tasks: their panes and the programs running in them."""

from __future__ import annotations

import os
import re
import secrets
import shlex
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from bytearmy import answers, themes
from bytearmy.agents import (
    AGENT_FILE,
    AGENT_INSTRUCTIONS,
    EARLIER_AGENT_INSTRUCTIONS,
    EARLIER_MASTER_INSTRUCTIONS,
    EARLIER_ORCHESTRATOR_INSTRUCTIONS,
    EARLIER_SCRATCHPAD_INSTRUCTIONS,
    MASTER,
    MASTER_FILE,
    MASTER_INSTRUCTIONS,
    ORCHESTRATOR_FILE,
    ORCHESTRATOR_INSTRUCTIONS,
    SCRATCHPAD_FILE,
    SCRATCHPAD_INSTRUCTIONS,
    agent_name,
    is_master,
    next_role,
    remove_legacy,
    write_instructions,
)
from bytearmy.answers import AnswerCommands, AnswerFailed
from bytearmy.config import ACCOUNTS, AGENT_COMMANDS, Config, config_of, ensure_config
from bytearmy.layout import add_leaf, leaves, remove_leaf
from bytearmy.merges import MergeCommands, MergeFailed
from bytearmy.models import (
    Agent,
    AgentSpawn,
    AnswerDraft,
    DiffMode,
    Draft,
    LogEntry,
    MergeStart,
    Pane,
    PaneCall,
    PaneOpen,
    PullRequestCreate,
    PullRequestMerge,
    ReviewRequest,
    ReviewState,
    Split,
    Task,
    TaskChange,
    Tool,
    WorkflowDispatch,
    Workspace,
)
from bytearmy.prs import PullRequestCommands, PullRequestFailed
from bytearmy.ptys import Pty
from bytearmy.pulls import PullRequests
from bytearmy.pycharm import give_interpreter, interpreter_name
from bytearmy.reviews import ReviewFailed, Reviews, default_branch
from bytearmy.services import Services, launch
from bytearmy.store import LOG_FILE, METADATA_DIR, ProjectNotFound, Store, TaskNotFound
from bytearmy.tools import (
    ToolFailed,
    as_configured,
    command_line,
    default_value,
    ensure_premade,
    external_command,
    load_tools,
    page_url,
    write_premade,
)
from bytearmy.trust import trust_directory
from bytearmy.workflows import WorkflowCommands, WorkflowFailed

PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"  # bracketed paste, as terminals send it
SUBMIT_DELAY_SECONDS = 0.3  # Claude takes a paste as such only if Enter comes after it
# A program that just started, such as a Claude session that resumes after a restart, is
# typed into only once it has run this long and then printed nothing for QUIET_SECONDS;
# before, what is typed may be lost. After READY_AT_LATEST_SECONDS it counts as ready.
READY_AFTER_SECONDS = 2.0
QUIET_SECONDS = 1.0
READY_AT_LATEST_SECONDS = 30.0
CLOSED_REVIEW = "The user closed the review without sending it; no comments were sent."
# A notifying program that ends this soon after an agent opened it is reported to the agent
# in the answer to its call, not as a message: it most likely failed, e.g. for a missing
# permission or login, and listens for nothing.
START_CHECK_SECONDS = 3.0
EVENT_LINES = 20  # how much of a listener's output its message holds
EVENT_CHARS = 2000
# Terminal control sequences: CSI, OSC and other escapes, and the lone control characters.
CONTROL = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|[\x00-\x08\x0b-\x1f\x7f]"
)
COULD_NOT_START = 127  # the exit code a shell gives for a command it cannot find
BROWSER_COMMAND = "bytearmy-browser"  # the browser of the panes, in their bin directory
SPAWNING_TOOL = "claude"  # starts agents for a pane that is not an agent itself

Key = tuple[str, str, str]  # project, task, pane


class UnknownTool(LookupError):
    pass


class NoAgentTool(LookupError):
    """No tool can start an agent with a prompt."""


class NotForAgents(PermissionError):
    """args: the id of a tool that only the user may open."""


class RoleTaken(ValueError):
    """args: a role that another agent of the task already has."""


class PaneNotFound(LookupError):
    pass


class NotStarted(ValueError):
    """args: the name of a task that is still in the backlog."""


class InvalidValues(ValueError):
    pass


class InvalidLayout(ValueError):
    pass


class NoDiffTool(LookupError):
    """No tool shows the changes of a task."""


class DraftDecided(ValueError):
    """A draft answer was already approved, discarded or sent; the message says how."""


class CommandNotAllowed(PermissionError):
    """args: the `bytearmy` command, then the table of config.toml whose `allow` lacks it."""


class NoMaster(LookupError):
    """The task has no running master agent to send a review to."""


def pane_env(environ: Mapping[str, str], prefix: str) -> dict[str, str]:
    """The user's environment without what the core's own environment manager added."""
    env = {
        name: value for name, value in environ.items() if not name.startswith(("PIXI_", "CONDA_"))
    }
    path = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and not entry.startswith(prefix)
    ]
    env["PATH"] = os.pathsep.join(path)
    env.update(TERM="xterm-256color", COLORTERM="truecolor", TERM_PROGRAM="bytearmy")
    return env


def pane_command(bin_dir: Path) -> Path:
    """Write the `bytearmy` command of the panes: this Python, running bytearmy.cli."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / "bytearmy"
    # -I: what lies in the directory the command is called from cannot replace a module.
    command.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -m bytearmy.cli "$@"\n')
    command.chmod(0o755)
    return command


def browser_command(bin_dir: Path) -> Path:
    """Write the browser of the panes, which BROWSER names: it opens a page a program asks
    for, e.g. `aws sso login`, in a webpage pane beside the program's pane. Where bytearmy
    cannot open one, the system's browser opens it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / BROWSER_COMMAND
    system = "open" if sys.platform == "darwin" else "xdg-open"
    bytearmy = shlex.quote(str(bin_dir / "bytearmy"))
    command.write_text(
        "#!/bin/sh\n"
        f'{bytearmy} pane open webpage --login "url=$1" >/dev/null 2>&1 || exec {system} "$1"\n'
    )
    command.chmod(0o755)
    return command


def loopback(url: str) -> bool:
    """Whether a page is served on this machine, as the end of a login is."""
    return urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}


class Workspaces:
    def __init__(
        self,
        store: Store,
        shell: Sequence[str] | None = None,
        url: str = "",
        pulls: PullRequests | None = None,
        pr_commands: PullRequestCommands | None = None,
        workflow_commands: WorkflowCommands | None = None,
        answer_commands: AnswerCommands | None = None,
        merge_commands: MergeCommands | None = None,
    ) -> None:
        self._store = store
        self._pulls = pulls or PullRequests(store)
        self._pr_commands = pr_commands or PullRequestCommands()
        self._workflow_commands = workflow_commands or WorkflowCommands()
        self._merge_commands = merge_commands or MergeCommands()
        self._answer_commands = answer_commands or AnswerCommands()
        # A login shell that reads the user's startup files: functions and aliases work.
        self._shell = list(shell or [os.environ.get("SHELL", "/bin/sh"), "-l", "-i"])
        self.url = url  # where the programs in the panes reach the core
        self._lock = threading.RLock()
        self._ptys: dict[Key, Pty] = {}
        self._ending: set[Key] = set()  # panes whose program is being ended on purpose
        # Notifying panes in their start check: set when the program ends, with how it ended.
        self._checking: dict[Key, tuple[threading.Event, list[Ended]]] = {}
        self._keys: dict[Key, str] = {}  # the key each pane's program calls the core with
        self._reviews: dict[Key, str] = {}  # finished reviews an agent asked for, until it reads
        self._stopping = False
        self._logs = store.root / METADATA_DIR / "logs"  # of programs without a terminal
        self._services = Services(self._logs)
        self._diffle = Reviews(self._logs)
        self._bin = pane_command(store.root / METADATA_DIR / "bin").parent
        browser_command(self._bin)
        ensure_premade(store.root)
        remove_legacy(store.root)
        write_premade(store.root / MASTER_FILE, MASTER_INSTRUCTIONS, EARLIER_MASTER_INSTRUCTIONS)
        write_premade(store.root / AGENT_FILE, AGENT_INSTRUCTIONS, EARLIER_AGENT_INSTRUCTIONS)
        write_premade(
            store.root / SCRATCHPAD_FILE, SCRATCHPAD_INSTRUCTIONS, EARLIER_SCRATCHPAD_INSTRUCTIONS
        )
        write_premade(
            store.root / ORCHESTRATOR_FILE,
            ORCHESTRATOR_INSTRUCTIONS,
            EARLIER_ORCHESTRATOR_INSTRUCTIONS,
        )
        themes.ensure_premade(store.root)
        ensure_config(store.root)

    def tools(self, project: str | None = None, task: str | None = None) -> list[Tool]:
        """The tools of a project, each parameter with what it defaults to there.

        In a `task`, {task_page} is its pull request once one is known.
        """
        found = self._store.project(project) if project else None
        github_page = found.github_page if found else ""
        pull_request = ""
        if found and task:
            pull_request = self._pulls.page(found, self._store.task(found.slug, task))
        return [
            tool.model_copy(
                update={
                    "parameters": [
                        parameter.model_copy(
                            update={"default": default_value(parameter, github_page, pull_request)}
                        )
                        for parameter in tool.parameters
                    ]
                }
            )
            for tool in self._tools(project, task).values()
        ]

    def config(self, project: str | None = None, task: str | None = None) -> Config:
        """The settings that hold in a task, a project or the whole root."""
        project_dir = Path(self._store.project(project).path) if project else None
        task_dir = Path(self._store.task(project, task).path) if project and task else None
        return config_of(self._store.root, project_dir, task_dir)

    def github_env(self, project: str | None = None, task: str | None = None) -> dict[str, str]:
        """What makes gh and git act as the GitHub account the config names."""
        return ACCOUNTS.env(self.config(project, task).github)

    def _tools(self, project: str | None, task: str | None = None) -> dict[str, Tool]:
        """The tools by id; a project's own tool files replace those of the root, and the
        config leaves out or changes some."""
        directory = Path(self._store.project(project).path) if project else None
        settings = self.config(project, task).tools
        return load_tools(self._store.root, directory, settings)

    # -- panes -------------------------------------------------------------

    def open_pane(
        self,
        project: str,
        task: str,
        request: PaneOpen,
        role: str | None = None,
        prompt: str = "",
        spawned_by: str = "",
        author: str = "user",
    ) -> Pane | LogEntry | None:
        """Add a pane to the task's layout. Its program starts when a terminal attaches.

        An external tool gets no pane: its program is started at once, in a window of its own.
        A log tool gets none either: it adds an entry by `author` to the task's log.
        An agent gets a name with its `role`; without one, the first agent of the task is
        its master. `prompt` and `spawned_by` are for an agent that another one starts.
        """
        tool = self._tools(project, task).get(request.tool)
        if tool is None:
            raise UnknownTool(request.tool)
        found = self._store.task(project, task)
        if found.state == "backlog":
            raise NotStarted(found.name)
        project_found = self._store.project(project)
        pull_request = ""
        if any(
            "{task_page}" in parameter.default
            and not request.values.get(parameter.name, "").strip()
            for parameter in tool.parameters
        ):
            pull_request = self._pulls.page(project_found, found, wait=True)
        values = _checked_values(
            tool, request.values, Path(found.path), project_found.github_page, pull_request
        )
        if tool.kind == "external":
            env = {**pane_env(os.environ, sys.prefix), **self.github_env(project, task)}
            if tool.interpreter and found.clone == "ready":
                repo = Path(found.path) / found.repo
                give_interpreter(repo, tool.interpreter, interpreter_name(project, task), env)
            launch(
                tool,
                external_command(tool, values),
                _directory(tool, found),
                env,
                self._logs / f"{tool.id}.log",
            )
            return None
        if tool.kind == "log":
            entry = LogEntry(
                time=datetime.now(UTC).replace(microsecond=0), author=author, text=values["entry"]
            )
            self._store.add_log_entry(project, task, entry)
            return entry
        if tool.kind == "web":
            _page(
                tool,
                values,
                found,
                files="http://files",
                service="http://service",
                log="http://log",
            )
        opened: list[Pane] = []

        def change(workspace: Workspace) -> None:
            pane = Pane(
                id=f"p{workspace.next_pane}",
                tool=tool.id,
                kind=tool.kind,
                title=": ".join([tool.name, *values.values()]),
                values=values,
                session=str(uuid.uuid4()),
                prompt=prompt,
                spawned_by=spawned_by,
                hidden=request.hidden,
                login=request.login,
            )
            if tool.agent:
                _make_agent(workspace, pane, (project, task), tool, role)
            workspace.next_pane += 1
            workspace.panes[pane.id] = pane
            if not pane.hidden:
                workspace.tree = add_leaf(workspace.tree, pane.id, request.beside)
            opened.append(pane)

        self._store.change_workspace(project, task, change)
        return opened[0]

    def close_pane(self, project: str, task: str, pane: str) -> None:
        """Close a pane. A diff pane closes without sending its review: its comments stay in
        diffle and show again in the next review."""
        found = self._store.task(project, task)
        if pane not in found.workspace.panes:
            raise PaneNotFound(pane)
        if found.workspace.panes[pane].kind == "diff":
            self._diffle.stop([(project, task, pane)])
        if found.workspace.panes[pane].kind == "answer":
            draft = answers.load(Path(found.path), pane)
            if draft is not None and draft.state == "open":
                self._decide(project, task, draft, approve=False)
        self._end([(project, task, pane)])
        self._store.change_workspace(project, task, lambda workspace: _drop(workspace, pane))

    def finish_review(self, project: str, task: str, pane: str) -> None:
        """Send the review of a diff pane to the agent that asked for it, or else to the
        master, and close the pane."""
        self._finish_review(project, task, pane)
        self._end([(project, task, pane)])
        self._store.change_workspace(project, task, lambda workspace: _drop(workspace, pane))

    def set_tree(self, project: str, task: str, tree: Split | str | None) -> None:
        """Take over a layout the user arranged; it must hold exactly the task's shown panes."""

        def change(workspace: Workspace) -> None:
            shown = [pane.id for pane in workspace.panes.values() if not pane.hidden]
            if sorted(leaves(tree)) != sorted(shown):
                raise InvalidLayout(
                    "the layout must hold every shown pane of the task exactly once"
                )
            workspace.tree = tree

        self._store.change_workspace(project, task, change)

    def maximize(self, project: str, task: str, pane: str | None) -> None:
        def change(workspace: Workspace) -> None:
            if pane is not None and pane not in workspace.panes:
                raise PaneNotFound(pane)
            if pane is not None and workspace.panes[pane].hidden:
                raise InvalidLayout(f"{pane} is hidden; show it before maximizing it")
            workspace.maximized = pane

        self._store.change_workspace(project, task, change)

    def set_hidden(
        self, project: str, task: str, pane: str, hidden: bool, beside: str | None = None
    ) -> None:
        """Take a pane out of the layout, or put it back beside the pane `beside`.

        A hidden pane's program runs on.
        """

        def change(workspace: Workspace) -> None:
            if pane not in workspace.panes:
                raise PaneNotFound(pane)
            if hidden:
                _hide(workspace, pane)
            else:
                _show(workspace, pane, beside)

        self._store.change_workspace(project, task, change)

    def set_address(self, project: str, task: str, pane: str, url: str) -> None:
        """Remember the page a web pane went to. A login pane closes once its page comes
        back to this machine: the program that opened it has then got what it waited for."""

        def change(workspace: Workspace) -> None:
            if pane not in workspace.panes:
                raise PaneNotFound(pane)
            workspace.panes[pane].address = url

        self._store.change_workspace(project, task, change)
        if self._store.task(project, task).workspace.panes[pane].login and loopback(url):
            self.close_pane(project, task, pane)

    def list_hidden(self, project: str, task: str, open: bool) -> None:
        """Unfold or fold the list of the task's hidden panes in the tasks bar."""

        def change(workspace: Workspace) -> None:
            workspace.hidden_open = open

        self._store.change_workspace(project, task, change)

    def highlight(
        self, project: str, task: str, pane: str, on: bool, beside: str | None = None
    ) -> None:
        """Highlight a pane, or end its highlight. A hidden pane is shown beside `beside`."""

        def change(workspace: Workspace) -> None:
            if pane not in workspace.panes:
                raise PaneNotFound(pane)
            workspace.panes[pane].highlighted = on
            if on:
                _show(workspace, pane, beside)

        self._store.change_workspace(project, task, change)

    def move(self, project: str, task: str, pane: str, beside: str) -> None:
        """Place a pane next to another one, splitting that one's place."""

        def change(workspace: Workspace) -> None:
            for wanted in (pane, beside):
                if wanted not in workspace.panes:
                    raise PaneNotFound(wanted)
            if pane == beside:
                raise InvalidLayout("a pane cannot be placed next to itself")
            for wanted in (pane, beside):
                if workspace.panes[wanted].hidden:
                    raise InvalidLayout(f"{wanted} is hidden; show it first")
            workspace.tree = add_leaf(remove_leaf(workspace.tree, pane), pane, beside)

        self._store.change_workspace(project, task, change)

    # -- calls from the programs in the panes ------------------------------

    def caller(self, key: str) -> Key | None:
        """The pane whose program calls with this key; None if no running program has it."""
        with self._lock:
            return next(
                (
                    pane
                    for pane, known in self._keys.items()
                    if secrets.compare_digest(known.encode(), key.encode())
                ),
                None,
            )

    def check_allowed(self, caller: Key, command: str) -> None:
        """Raise CommandNotAllowed if config.toml does not let the calling agent run a
        `bytearmy` command. A pane without an agent is used by the user: it may run all."""
        project, task, pane = caller
        found = self._store.task(project, task)
        calling = found.workspace.panes.get(pane)
        if calling is None or not calling.agent:
            return
        config = self.config(project, task)
        key = "master" if is_master(found, pane) else "agents"
        if not getattr(config, key).allows(command):
            raise CommandNotAllowed(command, key)

    def open_for(self, caller: Key, call: PaneCall) -> Pane | LogEntry | Ended | None:
        """Open a tool for the program of a pane, beside that pane, and start it at once.

        A log entry is written by the pane's agent; a pane without one is used by the user.
        A notifying tool is watched for START_CHECK_SECONDS: if its program ends by then, the
        answer tells how instead of a message.
        """
        project, task, pane = caller
        tool = self._tools(project, task).get(call.tool)
        if tool is None:
            raise UnknownTool(call.tool)
        if "agent" not in tool.callers:
            raise NotForAgents(tool.id)
        self.check_allowed(caller, "log" if tool.kind == "log" else "pane.open")
        request = PaneOpen(
            tool=tool.id,
            values=values_from_args(tool, call.args),
            beside=call.beside or pane,
            hidden=call.hidden,
            login=call.login,
        )
        calling = self._store.task(project, task).workspace.panes.get(pane)
        author = calling.agent if calling and calling.agent else "user"
        # A tool that notifies remembers who opened it: that agent gets its message.
        opener = author if tool.notify and author != "user" else ""
        opened = self.open_pane(project, task, request, spawned_by=opener, author=author)
        if isinstance(opened, Pane):
            if tool.notify and tool.kind == "terminal":
                return self._start_checked((project, task, opened.id), opened)
            self.start(project, task, opened.id)
        return opened

    def _start_checked(self, key: Key, pane: Pane) -> Pane | Ended:
        """Start a notifying pane and wait whether its program ends at once."""
        ended, result = threading.Event(), []
        with self._lock:
            self._checking[key] = (ended, result)
        try:
            self.start(*key)
            ended.wait(START_CHECK_SECONDS)
        finally:
            with self._lock:  # from here on, `_ended` sends its message as usual
                self._checking.pop(key, None)
        return result[0] if result else pane

    def spawn(self, caller: Key, request: AgentSpawn) -> Pane:
        """Start an agent with a prompt, in a hidden pane of its own; shown, it goes beside
        the calling one.

        It runs the calling pane's tool if that starts agents, the Claude tool otherwise.
        """
        project, task, pane = caller
        calling = self._store.task(project, task).workspace.panes.get(pane)
        tools = self._tools(project, task)
        settings = self.config(project, task).agents
        tool = tools.get(calling.tool) if calling else None
        if tool is None or not (tool.agent and as_configured(tool, settings).spawn):
            tool = tools.get(SPAWNING_TOOL)
        if tool is None or not (tool.agent and as_configured(tool, settings).spawn):
            raise NoAgentTool()
        opened = self.open_pane(
            project,
            task,
            PaneOpen(tool=tool.id, beside=request.beside or pane, hidden=True),
            role=request.role,
            prompt=request.prompt,
            spawned_by=calling.agent if calling else "",
        )
        assert isinstance(opened, Pane)  # an agent tool is never external, nor a log
        self.start(project, task, opened.id)
        return opened

    def start_master(
        self, project: str, task: str, prompt: str | None = None, spawned_by: str = ""
    ) -> Pane:
        """Open the master agent of a task, shown on its workspace, with `prompt` as its first
        message: by default the task's description; one without either just starts.

        The orchestrator passes its own prompt, and itself as `spawned_by`.
        """
        tool = self._tools(project, task).get(SPAWNING_TOOL)
        if tool is not None:
            tool = as_configured(tool, self.config(project, task).master)
        if tool is None or not tool.agent or (prompt and not tool.spawn):
            raise NoAgentTool()
        found = self._store.task(project, task)
        if found.workspace.master is not None:
            raise RoleTaken(MASTER)
        if prompt is None:
            prompt = found.description
        opened = self.open_pane(
            project,
            task,
            PaneOpen(tool=tool.id),
            role=MASTER,
            prompt=prompt if tool.spawn else "",
            spawned_by=spawned_by,
        )
        assert isinstance(opened, Pane)  # an agent tool is never external, nor a log
        self.start(project, task, opened.id)
        return opened

    def master_running(self, project: str, task: str) -> bool:
        """Whether the program of the task's master runs."""
        workspace = self._store.task(project, task).workspace
        with self._lock:
            return workspace.master is not None and self._running((project, task, workspace.master))

    @property
    def bin_dir(self) -> Path:
        """Where the `bytearmy` command of the panes lies."""
        return self._bin

    def request_review(self, caller: Key, request: ReviewRequest) -> Pane:
        """Open a highlighted diff pane beside the calling one; the review the user writes
        there is kept for the caller instead of being sent to the master."""
        project, task, pane = caller
        tools = self._tools(project, task)
        tool = tools.get("diff")
        if tool is None or tool.kind != "diff":
            tool = next((other for other in tools.values() if other.kind == "diff"), None)
        if tool is None:
            raise NoDiffTool()
        opened = self.open_pane(project, task, PaneOpen(tool=tool.id, beside=pane))
        assert isinstance(opened, Pane)

        def change(workspace: Workspace) -> None:
            shown = workspace.panes[opened.id]
            shown.values = {"requester": pane, "mode": request.mode, "note": request.note}
            shown.highlighted = True
            if request.note:
                shown.title = f"{tool.name}: {request.note}"

        self._store.change_workspace(project, task, change)
        return opened

    def review_state(self, caller: Key, pane: str) -> ReviewState:
        """Whether the review in a diff pane is finished; a finished one is handed out once."""
        project, task, _ = caller
        key = (project, task, pane)
        with self._lock:
            if key in self._reviews:
                return ReviewState(done=True, text=self._reviews.pop(key))
        if pane in self._store.task(project, task).workspace.panes:
            return ReviewState(done=False)
        return ReviewState(done=True, text=CLOSED_REVIEW)

    # -- draft answers -----------------------------------------------------

    def draft_answer(self, caller: Key, request: AnswerDraft) -> Pane:
        """Fetch the conversation a GitHub URL points at and open it with the calling
        agent's draft answer in a highlighted answer pane beside the caller; the user
        approves or discards it there."""
        project, task, pane = caller
        found = self._store.task(project, task)
        calling = found.workspace.panes.get(pane)
        agent = calling.agent if calling else ""
        draft = self._answer_commands.conversation(
            request.url, Path(found.path), self.github_env(project, task), request.body, agent
        )
        opened: list[Pane] = []

        def change(workspace: Workspace) -> None:
            shown = Pane(
                id=f"p{workspace.next_pane}",
                tool="",
                kind="answer",
                title=f"Draft answer: {draft.title or request.url}",
                values={"url": draft.url},
                spawned_by=agent,
                highlighted=True,
            )
            workspace.next_pane += 1
            workspace.panes[shown.id] = shown
            workspace.tree = add_leaf(workspace.tree, shown.id, pane)
            draft.id = shown.id
            answers.save(Path(found.path), draft)
            opened.append(shown)

        self._store.change_workspace(project, task, change)
        self._log(project, task, f"{agent or 'An agent'} drafted an answer to {draft.url}")
        return opened[0]

    def draft(self, project: str, task: str, pane: str) -> Draft:
        """The draft answer an answer pane shows."""
        found = self._store.task(project, task)
        draft = answers.load(Path(found.path), pane)
        if draft is None:
            raise PaneNotFound(pane)
        return draft

    def decide_answer(
        self, project: str, task: str, pane: str, approve: bool, answer: str = ""
    ) -> None:
        """The user approves the draft in an answer pane, as they edited it, or discards
        it: the agent that drafted it is told, and the pane closes."""
        draft = self.draft(project, task, pane)
        if draft.state != "open":
            raise DraftDecided(f"the draft answer {pane} was already {draft.state}")
        if approve and not answer.strip():
            raise InvalidValues("The answer is empty.")
        self._decide(project, task, draft, approve, answer)
        self._end([(project, task, pane)])
        self._store.change_workspace(project, task, lambda workspace: _drop(workspace, pane))

    def _decide(
        self, project: str, task: str, draft: Draft, approve: bool, answer: str = ""
    ) -> None:
        edited = approve and answer.strip() != draft.draft.strip()
        draft.state = "approved" if approve else "discarded"
        draft.answer = answer if approve else ""
        answers.save(Path(self._store.task(project, task).path), draft)
        if approve:
            text = answers.approved_message(draft, edited)
            done = "approved" + (" an edited version of" if edited else "")
        else:
            text = answers.discarded_message(draft)
            done = "discarded"
        try:
            told = self.send_to_agent(project, task, draft.agent, text)
        except NoMaster:
            self._log(
                project, task, f"The user {done} the draft answer to {draft.url}; no agent to tell"
            )
            return
        self._log(project, task, f"The user {done} the draft answer to {draft.url}; told {told}")

    def send_answer(self, caller: Key, draft_id: str) -> str:
        """Post an approved answer to its conversation on GitHub, then set the task to
        waiting for the reply."""
        project, task, _ = caller
        found = self._store.task(project, task)
        draft = answers.load(Path(found.path), draft_id)
        if draft is None:
            raise AnswerFailed(f"there is no draft answer {draft_id}")
        if draft.state == "sent":
            raise DraftDecided(f"the answer {draft_id} was already sent: {draft.sent_url}")
        if draft.state != "approved":
            raise DraftDecided(
                f"the draft answer {draft_id} is {draft.state}, not approved: only an answer "
                "the user approved can be sent"
            )
        url = self._answer_commands.send(draft, Path(found.path), self.github_env(project, task))
        draft.state, draft.sent_url = "sent", url
        answers.save(Path(found.path), draft)
        self._log(project, task, f"Sent the answer to {draft.url}: {url}")
        sent = f"sent the answer: {url or draft.url}"
        if found.scratchpad:  # it has no state
            return sent
        self._store.change_task(
            project, task, TaskChange(state="waiting", status="Waiting for a reply on GitHub")
        )
        return sent + "\nthe task now waits for a reply on GitHub (state waiting)"

    # -- pull requests -----------------------------------------------------

    def _clone_and_branch(self, caller: Key) -> tuple[Path, str, dict[str, str]]:
        """Where the task's clone is, its branch, and the GitHub account to act as."""
        project, task, _ = caller
        found = self._store.task(project, task)
        if not found.branch:
            raise PullRequestFailed("the task has no branch, so it can have no pull request")
        if found.clone != "ready" or not found.repo:
            raise PullRequestFailed("the task has no clone of a GitHub repository")
        return Path(found.path) / found.repo, found.branch, self.github_env(project, task)

    def create_pull_request(self, caller: Key, request: PullRequestCreate) -> str:
        """Push the task's branch and open its pull request; gives its URL."""
        clone, branch, env = self._clone_and_branch(caller)
        url = self._pr_commands.create(
            clone, branch, env, request.title, request.body, request.base, request.draft
        )
        self._store.changed()  # the task bar links the pull request once it is found
        return url

    def view_pull_request(self, caller: Key) -> str:
        clone, branch, env = self._clone_and_branch(caller)
        return self._pr_commands.view(clone, branch, env)

    def merge_pull_request(self, caller: Key, request: PullRequestMerge) -> str:
        clone, branch, env = self._clone_and_branch(caller)
        return self._pr_commands.merge(clone, branch, env, request.method, request.delete_branch)

    # -- workflows ---------------------------------------------------------

    def _clone(self, caller: Key) -> tuple[Path, str, dict[str, str]]:
        """Where the task's clone is, its branch ("" if none), and the GitHub account."""
        project, task, _ = caller
        found = self._store.task(project, task)
        if found.clone != "ready" or not found.repo:
            raise WorkflowFailed("the task has no clone of a GitHub repository")
        return Path(found.path) / found.repo, found.branch, self.github_env(project, task)

    def list_workflows(self, caller: Key) -> str:
        clone, _, env = self._clone(caller)
        return self._workflow_commands.list(clone, env)

    def run_workflow(self, caller: Key, request: WorkflowDispatch) -> str:
        """Dispatch a workflow; it runs on the task's branch unless the request names a ref."""
        clone, branch, env = self._clone(caller)
        ref = request.ref or branch
        return self._workflow_commands.run(clone, env, request.workflow, ref, request.inputs)

    def workflow_runs(self, caller: Key, workflow: str, branch: str, limit: int) -> str:
        clone, _, env = self._clone(caller)
        return self._workflow_commands.runs(clone, env, workflow, branch, limit)

    # -- merges ------------------------------------------------------------

    def _merge_clone(self, caller: Key) -> tuple[Path, str, dict[str, str]]:
        """Where the task's clone is, its base branch ("" if none), and the GitHub account."""
        project, task, _ = caller
        found = self._store.task(project, task)
        if found.clone != "ready" or not found.repo:
            raise MergeFailed("the task has no clone of a GitHub repository")
        return Path(found.path) / found.repo, found.base, self.github_env(project, task)

    def start_merge(self, caller: Key, request: MergeStart) -> str:
        """Merge a branch of origin into the clone: the request's, else the task's base
        branch, else the default branch."""
        clone, base, env = self._merge_clone(caller)
        branch = request.branch or base
        if not branch:
            default = default_branch(clone)
            if default is None:
                raise MergeFailed("the repository has no default branch; name the branch")
            branch = default.removeprefix("origin/")
        return self._merge_commands.start(clone, env, branch)

    def continue_merge(self, caller: Key) -> str:
        clone, _, env = self._merge_clone(caller)
        return self._merge_commands.resume(clone, env)

    def abort_merge(self, caller: Key) -> str:
        clone, _, env = self._merge_clone(caller)
        return self._merge_commands.abort(clone, env)

    def _review_page(self, project: str, task: str, pane: str) -> str:
        """Where the diffle of a diff pane answers; starts it if it does not run yet."""
        found, shown, tool = self._diff_pane(project, task, pane)
        return self._diffle.url(
            (project, task, pane),
            _directory(tool, found),
            _mode(shown),
            self._diffle_env(),
            found.base,
        )

    def _finish_review(self, project: str, task: str, pane: str) -> None:
        """Send the review of a closing diff pane: to the agent that asked for it, or else
        to the master. Without a master to send it to, its comments stay in diffle and
        show again in the next review."""
        found, shown, tool = self._diff_pane(project, task, pane)
        key = (project, task, pane)
        requester = shown.values.get("requester")
        if not requester and not self.master_running(project, task):
            self._diffle.stop([key])
            self._log(project, task, "Closed a review; no master agent runs to send it to")
            return
        try:
            text, count = self._diffle.finish(
                key, _directory(tool, found), _mode(shown), self._diffle_env(), found.base
            )
        except (ToolFailed, ReviewFailed) as error:
            self._diffle.stop([key])
            self._log(project, task, f"Closed a review whose comments could not be read: {error}")
            return
        self._diffle.stop([key])
        if requester:
            with self._lock:
                self._reviews[key] = text
        else:
            self.send_to_master(project, task, text)
        self._log(project, task, f"Finished a review with {count} comment{'s' * (count != 1)}")

    def _diffle_env(self) -> dict[str, str]:
        return {**pane_env(os.environ, sys.prefix), **self.github_env()}

    def _log(self, project: str, task: str, text: str) -> None:
        entry = LogEntry(time=datetime.now(UTC).replace(microsecond=0), author="user", text=text)
        self._store.add_log_entry(project, task, entry)

    def send_to_master(self, project: str, task: str, text: str) -> None:
        """Type a message into the terminal of the task's master, as if the user wrote it."""
        master = self._store.task(project, task).workspace.master
        with self._lock:
            pty = self._ptys.get((project, task, master)) if master else None
            if pty is None or pty.exit_code is not None:
                raise NoMaster()
        _type_into(pty, text)

    def send_to_agent(self, project: str, task: str, agent: str, text: str) -> str:
        """Type a message into the terminal of an agent of the task; if it does not run,
        into the master's. Gives the name of the agent that got it."""
        workspace = self._store.task(project, task).workspace
        with self._lock:
            for pane in workspace.panes.values():
                if agent and pane.agent == agent and self._running((project, task, pane.id)):
                    _type_into(self._ptys[(project, task, pane.id)], text)
                    return agent
        self.send_to_master(project, task, text)
        return workspace.panes[workspace.master].agent if workspace.master else ""

    def _notify(self, key: Key, pane: Pane, code: int, output: bytes) -> None:
        """Tell the agent that opened a notifying pane that its program ended."""
        project, task, pane_id = key
        event = pane.values.get("event") or pane.title
        text = f"Event: {event}. The event listener in pane {pane_id} ended with exit code {code}."
        if pane.values.get("command"):
            text += f" Its command was: {pane.values['command']}"
        tail = _tail(output)
        if tail:
            text += f"\n\nThe last lines it printed:\n\n{tail}"
        try:
            told = self.send_to_agent(project, task, pane.spawned_by, text + "\n")
        except NoMaster:
            self._log(project, task, f"{event}: happened, but no agent runs to tell")
            return
        self._log(project, task, f"{event}: happened (exit code {code}); told {told}")

    def _diff_pane(self, project: str, task: str, pane: str) -> tuple[Task, Pane, Tool]:
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is None or shown.kind != "diff":
            raise PaneNotFound(pane)
        tool = self._tools(project, task).get(shown.tool)
        if tool is None or tool.kind != "diff":
            raise UnknownTool(shown.tool)
        return found, shown, tool

    def agents(self, project: str, task: str) -> list[Agent]:
        workspace = self._store.task(project, task).workspace
        with self._lock:
            return [
                Agent(
                    name=pane.agent,
                    pane=pane.id,
                    master=pane.id == workspace.master,
                    running=self._running((project, task, pane.id)),
                )
                for pane in workspace.panes.values()
                if pane.agent
            ]

    def _running(self, key: Key) -> bool:
        pty = self._ptys.get(key)
        return pty is not None and pty.exit_code is None

    # -- programs ----------------------------------------------------------

    def attach(self, project: str, task: str, pane: str, rows: int, cols: int) -> Pty | None:
        """The program of a pane, started now if the pane is new.

        None if the pane's program ended with an error in an earlier run.
        """
        with self._lock:
            found = self._store.task(project, task).workspace.panes.get(pane)
            if found is None or found.kind != "terminal":
                raise PaneNotFound(pane)
            key = (project, task, pane)
            if key not in self._ptys and found.exit_code is None:
                self._start(key, found, rows, cols, restoring=False)
            return self._ptys.get(key)

    def start(self, project: str, task: str, pane: str) -> None:
        """Start the program of a new terminal pane now, not when the window first shows it."""
        with self._lock:
            found = self._store.task(project, task).workspace.panes[pane]
            key = (project, task, pane)
            if found.kind == "terminal" and key not in self._ptys:
                self._start(key, found, found.rows, found.cols, restoring=False)

    def resized(self, project: str, task: str, pane: str, rows: int, cols: int) -> None:
        """Remember the size, so a restored program starts at the size it had."""

        def change(workspace: Workspace) -> None:
            if pane in workspace.panes:
                workspace.panes[pane].rows = rows
                workspace.panes[pane].cols = cols

        self._store.change_workspace(project, task, change)

    def start_all(self) -> None:
        """Start the program of every pane of every task, as after a restart."""
        with self._lock:
            for project in self._store.app_state().projects:
                for task in project.tasks:
                    for pane in task.workspace.panes.values():
                        key = (project.slug, task.slug, pane.id)
                        if pane.kind != "terminal":
                            continue  # a page is loaded by the pane that shows it
                        if key not in self._ptys and pane.exit_code is None:
                            self._start(key, pane, pane.rows, pane.cols, restoring=True)

    def _start(self, key: Key, pane: Pane, rows: int, cols: int, restoring: bool) -> None:
        project, task, pane_id = key
        tool = self._tools(project, task).get(pane.tool)
        if tool is None:
            self._ended(key, COULD_NOT_START)
            return
        if tool.agent and not pane.agent:  # opened before its tool started agents
            self._store.change_workspace(
                project,
                task,
                lambda workspace: _make_agent(
                    workspace, workspace.panes[pane_id], (project, task), tool, None
                ),
            )
        found = self._store.task(project, task)
        pane = found.workspace.panes[pane_id]
        instructions = None
        permissions = ""
        if tool.agent:
            config = self.config(project, task)
            settings = config.master if is_master(found, pane_id) else config.agents
            tool = as_configured(tool, settings)
            permissions = settings.flags()
            instructions = write_instructions(
                self._store.root,
                found,
                pane,
                settings.denied(AGENT_COMMANDS),
                settings.forbidden_actions(),
                self._listeners_of(project, task, found.workspace, pane),
                Path(self._store.project(project).path),
            )
        line = command_line(tool, pane, restoring, instructions, permissions)
        argv = [*self._shell, "-c", line] if line else self._shell
        directory = _directory(tool, found)
        if tool.agent:
            trust_directory(directory)  # Claude would first ask whether to trust it
        self._ptys[key] = Pty(
            argv,
            directory,
            self._env(key),
            rows,
            cols,
            on_exit=lambda code: self._ended(key, code),
        )

    def _listeners_of(
        self, project: str, task: str, workspace: Workspace, agent: Pane
    ) -> list[Pane]:
        """The notifying panes whose message goes to an agent: those it opened, and for the
        master also those whose opener is gone."""
        tools = self._tools(project, task)
        agents = {pane.agent for pane in workspace.panes.values() if pane.agent}
        is_master = workspace.master == agent.id
        return [
            pane
            for pane in workspace.panes.values()
            if (tool := tools.get(pane.tool)) is not None
            and tool.notify
            and pane.exit_code is None
            and (pane.spawned_by == agent.agent or (is_master and pane.spawned_by not in agents))
        ]

    def _env(self, key: Key) -> dict[str, str]:
        """The environment of a pane's program: the user's, and how to reach the core.

        The key it gets lets the core tell which pane calls; it lasts as long as the program.
        """
        project, task, pane = key
        self._keys[key] = secrets.token_urlsafe(24)
        env = pane_env(os.environ, sys.prefix)
        env["PATH"] = os.pathsep.join(filter(None, [str(self._bin), env["PATH"]]))
        env.update(
            BYTEARMY_URL=self.url,
            BYTEARMY_TOKEN=self._keys[key],
            BYTEARMY_TASK=f"{project}/{task}",
            BYTEARMY_PANE=pane,
        )
        if self.config(project, task).browser_in_pane:
            env["BROWSER"] = str(self._bin / BROWSER_COMMAND)
        env.update(self.github_env(project, task))
        return env

    def page(
        self, project: str, task: str, pane: str, files: str, log: str = "", answers: str = ""
    ) -> str:
        """The page a web pane shows; `files` is where the task directory is served, `log`
        where the task's log is shown.

        Starts the service of the pane's tool if it does not run yet, which can take a while.
        A diff pane shows the diffle started for it.
        """
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is not None and shown.kind == "diff":
            return self._review_page(project, task, pane)
        if shown is not None and shown.kind == "answer":
            return f"{answers}/{pane}"  # `answers` is where the core serves their pages
        if shown is None or shown.kind != "web":
            raise PaneNotFound(pane)
        if shown.tool == "webpage" and shown.address.startswith(("http://", "https://")):
            return shown.address  # the page it showed last, which a restart opens again
        tool = self._tools(project, task).get(shown.tool)
        if tool is None or tool.kind != "web":
            raise UnknownTool(shown.tool)
        service = ""
        if tool.service is not None:
            service = self._services.url(
                tool, {**pane_env(os.environ, sys.prefix), **self.github_env()}
            )
        return _page(tool, shown.values, found, files, service, log)

    def page_version(self, project: str, task: str, pane: str) -> str | None:
        """A text that changes when a file the web pane shows changes; None if it shows none.

        The files a pane shows are the values of its tool's path parameters, and the
        task's log if its page is the log.
        """
        found = self._store.task(project, task)
        shown = found.workspace.panes.get(pane)
        if shown is not None and shown.kind in ("diff", "answer"):
            return None  # diffle follows the changes itself; a reload would lose edits
        if shown is None or shown.kind != "web":
            raise PaneNotFound(pane)
        tool = self._tools(project, task).get(shown.tool)
        files = [
            Path(found.path) / shown.values[parameter.name]
            for parameter in (tool.parameters if tool else [])
            if parameter.path and parameter.name in shown.values
        ]
        if tool and "{log}" in tool.url:
            # The log page has the colors of the window.
            files.append(Path(found.path) / METADATA_DIR / LOG_FILE)
            return " ".join([*(_stamp(file) for file in files), themes.stamp(self._store.root)])
        return " ".join(_stamp(file) for file in files) if files else None

    def _ended(self, key: Key, code: int) -> None:
        """A program ended by itself: close its pane if all went well, keep it otherwise."""
        project, task, pane = key
        with self._lock:
            self._keys.pop(key, None)
            if self._stopping or key in self._ending:
                return
            try:
                ended = self._store.task(project, task).workspace.panes.get(pane)
            except (ProjectNotFound, TaskNotFound):
                return  # deleted meanwhile
            tool = self._tools(project, task).get(ended.tool) if ended else None
            pty = self._ptys.get(key)
            notifies = ended is not None and tool is not None and tool.notify
            if notifies:
                output = pty.output() if pty else b""
                checking = self._checking.get(key)
                if checking is not None:  # the agent that opened it is still told in the answer
                    checking[1].append(Ended(pane, code, _tail(output)))
                    checking[0].set()
                    event = ended.values.get("event") or ended.title
                    self._log(
                        project, task, f"{event}: the listener ended at once (exit code {code})"
                    )
                else:
                    self._notify(key, ended, code, output)
                code = 0  # its message tells how it ended: the pane is no longer needed

            def change(workspace: Workspace) -> None:
                if pane not in workspace.panes:
                    return
                if code == 0:
                    _drop(workspace, pane)
                else:
                    workspace.panes[pane].exit_code = code

            if code == 0:
                self._ptys.pop(key, None)
            try:
                self._store.change_workspace(project, task, change)
            except (ProjectNotFound, TaskNotFound):
                pass  # deleted meanwhile

    def _end(self, keys: list[Key]) -> None:
        """End programs on purpose and wait for them; their panes are not touched."""
        with self._lock:
            self._ending.update(keys)
            ptys = [pty for key in keys if (pty := self._ptys.pop(key, None)) is not None]
        for pty in ptys:  # outside the lock: an ending program reports through `_ended`
            pty.hang_up()
        for pty in ptys:
            pty.kill()
        with self._lock:
            self._ending.difference_update(keys)

    def stop(self, project: str, task: str | None = None) -> None:
        """End the programs of one task, or of every task of the project."""
        with self._lock:
            keys = [key for key in self._ptys if key[0] == project and task in (None, key[1])]
        self._end(keys)
        self._diffle.stop(
            [key for key in self._diffle.running() if key[0] == project and task in (None, key[1])]
        )

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            keys = list(self._ptys)
        self._end(keys)
        self._services.shutdown()
        self._diffle.shutdown()


@dataclass
class Ended:
    """A notifying pane whose program ended during its start check."""

    pane: str
    code: int
    output: str  # its last lines


def _ready_in(pty: Pty) -> float:
    """Seconds until a program takes typing; 0 if it does now."""
    now = time.monotonic()
    age = now - pty.started
    if age >= READY_AT_LATEST_SECONDS:
        return 0
    wait = max(READY_AFTER_SECONDS - age, QUIET_SECONDS - (now - pty.last_output), 0)
    return min(wait, READY_AT_LATEST_SECONDS - age)


def _type_into(pty: Pty, text: str) -> None:
    """Paste a message into an agent's terminal and send it, as if the user wrote it.

    An agent that is still starting gets it once it is ready (see READY_AFTER_SECONDS).
    """
    delay = _ready_in(pty)
    if delay > 0:
        later = threading.Timer(delay, _type_into, args=(pty, text))
        later.daemon = True
        later.start()
        return
    clean = text.replace("\x1b", "").replace("\r\n", "\n").encode()
    pty.write(PASTE_START + clean + PASTE_END)
    submit = threading.Timer(SUBMIT_DELAY_SECONDS, pty.write, args=(b"\r",))
    submit.daemon = True
    submit.start()


def _tail(output: bytes) -> str:
    """The last lines a program printed, without the terminal's control sequences."""
    text = output.decode(errors="replace").replace("\r\n", "\n")
    # A line rewritten in place, such as a progress bar, counts as what it says last.
    lines = [
        CONTROL.sub("", line.rstrip("\r").split("\r")[-1]).rstrip() for line in text.split("\n")
    ]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines[-EVENT_LINES:])[-EVENT_CHARS:]


def _directory(tool: Tool, task: Task) -> Path:
    """Where a tool starts: the clone if it asks for it and the task has one."""
    directory = Path(task.path)
    if tool.directory == "repo" and task.clone == "ready":
        directory /= task.repo
    return directory


def _mode(pane: Pane) -> DiffMode:
    """What a diff pane compares: the whole branch, unless it asks for the uncommitted changes."""
    return "uncommitted" if pane.values.get("mode") == "uncommitted" else "branch"


def _stamp(file: Path) -> str:
    """Differs after the file was written again; time and size, as either can stay the same."""
    try:
        status = file.stat()
    except OSError:
        return "missing"
    return f"{status.st_mtime_ns}-{status.st_size}"


def _page(
    tool: Tool, values: Mapping[str, str], task: Task, files: str, service: str, log: str
) -> str:
    try:
        return page_url(tool, values, _directory(tool, task), files, service, log)
    except ValueError as error:
        raise InvalidValues(str(error)) from error


def _hide(workspace: Workspace, pane: str) -> None:
    workspace.panes[pane].hidden = True
    workspace.tree = remove_leaf(workspace.tree, pane)
    if workspace.maximized == pane:
        workspace.maximized = None


def _show(workspace: Workspace, pane: str, beside: str | None) -> None:
    """Put a hidden pane back into the layout, splitting `beside`; a shown one stays put."""
    if workspace.panes[pane].hidden:
        workspace.panes[pane].hidden = False
        workspace.tree = add_leaf(workspace.tree, pane, beside)


def _drop(workspace: Workspace, pane: str) -> None:
    workspace.panes.pop(pane, None)
    workspace.tree = remove_leaf(workspace.tree, pane)
    if workspace.maximized == pane:
        workspace.maximized = None
    if workspace.master == pane:
        workspace.master = None


def _make_agent(
    workspace: Workspace, pane: Pane, place: tuple[str, str], tool: Tool, role: str | None
) -> None:
    """Name the agent of a pane. Without a role it is the master, if the task has none."""
    prefix = agent_name(*place, "")
    roles = {other.agent.removeprefix(prefix) for other in workspace.panes.values() if other.agent}
    if role is None:
        role = next_role(roles) if workspace.master is not None else MASTER
    if role in roles:
        raise RoleTaken(role)
    pane.agent = agent_name(*place, role)
    pane.title = f"{tool.name}: {role}"
    if role == MASTER:
        workspace.master = pane.id


def values_from_args(tool: Tool, args: Sequence[str]) -> dict[str, str]:
    """The parameter values a command line gives: `name=value`, or by position."""
    names = [parameter.name for parameter in tool.parameters]
    values: dict[str, str] = {}
    by_position = []
    for arg in args:
        name, equals, value = arg.partition("=")
        if equals and name in names and name not in values:
            values[name] = value
        else:
            by_position.append(arg)
    free = [name for name in names if name not in values]
    if len(by_position) > len(free):
        takes = ", ".join(names) or "nothing"
        raise InvalidValues(f"{tool.id} takes {takes}; got {' '.join(args)}")
    values.update(zip(free, by_position, strict=False))
    return values


def _checked_values(
    tool: Tool, given: Mapping[str, str], task_dir: Path, github_page: str, pull_request: str = ""
) -> dict[str, str]:
    """The values of the tool's parameters; every parameter needs one."""
    values = {}
    for parameter in tool.parameters:
        value = given.get(parameter.name, "").strip() or default_value(
            parameter, github_page, pull_request
        )
        if not value:
            raise InvalidValues(f"{parameter.label or parameter.name} is required.")
        if parameter.path and not (task_dir / value).resolve().is_relative_to(task_dir.resolve()):
            raise InvalidValues(f"{value} is not inside the task directory.")
        values[parameter.name] = value
    return values
