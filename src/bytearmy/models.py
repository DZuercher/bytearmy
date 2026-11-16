"""The data bytearmy keeps on disk and exchanges with the UI."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BACKLOG = "Backlog"
NEW = "New"
SCRATCHPAD = "Scratchpad"  # the name of a project's scratchpad task
SCRATCHPAD_STATUS = "Explore"

Color = Literal["", "red", "amber", "green", "blue", "purple"]  # of a task's tab; "" is none


class ProjectMeta(BaseModel):
    """What the user says about a project."""

    name: str
    description: str = ""
    github_url: str = ""

    @field_validator("name", "description", "github_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a project name must be provided")
        return value


class ProjectEdit(BaseModel):
    """What the user may change about a project; its GitHub URL is fixed once its tasks are cloned."""

    name: str
    description: str = ""

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a project name must be provided")
        return value


class ProjectFile(ProjectMeta):
    """Contents of `<project>/.bytearmy/project.json`."""

    backlog_order: list[str] = []
    task_order: list[str] = []
    focused_task: str | None = None


class TaskCreate(BaseModel):
    """What the user says about a task."""

    name: str
    description: str = ""
    branch: str = ""
    base: str = ""  # the branch a new branch starts from; empty: the default branch
    reminder: date | None = None
    dependencies: list[str] = []  # slugs of tasks in the same project

    @field_validator("name", "description", "branch", "base")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a task name must be provided")
        return value


class TaskEdit(BaseModel):
    """What the user may change about a task.

    A task in the backlog keeps the status Backlog, a started one cannot go back there.
    A task without a branch may get one; an empty branch keeps the task's, which is fixed
    once it is set.
    """

    name: str
    description: str = ""
    branch: str = ""
    status: str
    color: Color = ""
    reminder: date | None = None
    dependencies: list[str] = []  # slugs of tasks in the same project

    @field_validator("name", "description", "branch", "status")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a task name must be provided")
        return value

    @field_validator("status")
    @classmethod
    def _status_given(cls, value: str) -> str:
        if not value:
            raise ValueError("a status must not be empty")
        return value


class TaskFile(TaskCreate):
    """Contents of `<task>/.bytearmy/task.json`."""

    status: str = BACKLOG
    color: Color = ""  # set by an agent to get the user's attention
    repo: str = ""  # directory of the clone below the task directory; empty without a GitHub URL
    clone_error: str = ""
    # the project's scratchpad: always there, first in the task bar, for exploring the
    # project and asking general questions; it cannot be deleted
    scratchpad: bool = False


class Split(BaseModel):
    """A node of a workspace's layout tree, in the form react-mosaic draws."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["split"] = "split"
    direction: Literal["row", "column"]
    children: list[Split | str]  # a string is a leaf: the id of a pane
    split_percentages: list[float] | None = Field(default=None, alias="splitPercentages")


class Pane(BaseModel):
    id: str
    tool: str
    # a program in a terminal, a page, or the changes of the task's clone to review
    kind: Literal["terminal", "web", "diff"] = "terminal"
    title: str
    values: dict[str, str] = {}  # the tool's parameters, e.g. the file for Vim
    session: str = ""  # stays the same across restarts; lets a tool such as Claude resume
    agent: str = ""  # the session name of the agent in the pane; empty if it holds none
    prompt: str = ""  # what the agent that spawned this one asked it to do
    spawned_by: str = ""  # the name of that agent; empty if a person started this one
    highlighted: bool = False  # an agent wants the user to look at the pane
    hidden: bool = False  # not in the layout; its program runs on, the tasks bar lists it
    rows: int = 24
    cols: int = 80
    exit_code: int | None = None  # set when the program ended with an error


class Workspace(BaseModel):
    """Contents of `<task>/.bytearmy/layout.json`."""

    tree: Split | str | None = None
    panes: dict[str, Pane] = {}
    maximized: str | None = None
    master: str | None = None  # the pane of the task's master agent
    hidden_open: bool = False  # the tasks bar lists the hidden panes below the task
    next_pane: int = 1


class Task(TaskFile):
    slug: str  # directory name below the project; the task's id within it
    path: str
    clone: Literal["none", "cloning", "ready", "failed"]
    reminder_due: bool
    blocked_by: list[str]  # names of the dependencies that are still in the backlog
    workspace: Workspace


class Parameter(BaseModel):
    name: str
    label: str = ""
    path: bool = False  # the value is a file below the task directory
    default: str = ""  # used if nothing is entered; {github_page} is the project's page


class Service(BaseModel):
    """A program in the background that all panes of a web tool share."""

    command: str  # run by /bin/sh; {port} and {token} are chosen by bytearmy
    url: str  # where it then answers, e.g. `http://127.0.0.1:{port}/?tkn={token}`


class Tool(BaseModel):
    """A tool the palette offers; one file in `<root>/.tools` or `<project>/.tools`."""

    id: str
    name: str
    # terminal: a program in a terminal pane. web: a page in a browser pane.
    # external: a program with a window of its own, no pane.
    # log: adds its parameter `entry` to the task's log, no pane.
    # diff: the changes of the task's clone in diffle, for the user to review and comment on.
    kind: Literal["terminal", "web", "external", "log", "diff"] = "terminal"
    command: str = ""  # run by the user's shell; for a terminal, empty means the shell itself
    resume: str = ""  # run instead of `command` when a pane is restored after a restart
    agent: bool = False  # the program is an agent: it gets a name and bytearmy's instructions
    spawn: str = ""  # run instead of `command` when an agent starts this one with a prompt
    url: str = ""  # the page of a web tool
    directory: Literal["task", "repo"] = "task"  # repo: the clone, if the task has one
    callers: list[Literal["user", "agent"]] = ["user", "agent"]  # who may open the tool
    # terminal: when its program ends, tell the agent that opened it, or else the master
    notify: bool = False
    parameters: list[Parameter] = []
    service: Service | None = None

    @model_validator(mode="after")
    def _complete(self) -> Tool:
        if self.kind == "web" and not self.url:
            raise ValueError("a web tool needs a url")
        if self.kind == "external" and not self.command:
            raise ValueError("an external tool needs a command")
        if self.kind == "log" and "entry" not in {parameter.name for parameter in self.parameters}:
            raise ValueError("a log tool needs a parameter named entry")
        return self


DiffMode = Literal["branch", "uncommitted"]  # against the default branch, or against HEAD


class LogEntry(BaseModel):
    """One line of `<task>/.bytearmy/log.jsonl`."""

    time: datetime  # UTC
    author: str  # the name of the agent that wrote it, or "user"
    text: str


class Project(ProjectMeta):
    slug: str  # directory name below the root; the project's id
    path: str
    github_page: str  # the GitHub URL as a browser can show it; empty if it has none
    backlog: list[Task]
    tasks: list[Task]
    focused_task: str | None


class UiState(BaseModel):
    left_open: bool = True
    right_open: bool = True
    bottom_open: bool = True
    # focus mode: every bar shows collapsed and the inbox hides its unread count;
    # the *_open flags keep what the bars show once it ends
    focus: bool = False
    # what the middle of the window shows
    view: Literal["workspace", "inbox", "orchestrator"] = "workspace"


class UiChange(BaseModel):
    """The bars to open or close; a bar that is not named keeps its state."""

    left_open: bool | None = None
    right_open: bool | None = None
    bottom_open: bool | None = None
    focus: bool | None = None
    view: Literal["workspace", "inbox", "orchestrator"] | None = None


class Message(BaseModel):
    """One message of the inbox, as a source reported it."""

    id: str  # "<source>:<the source's own id>"
    source: Literal["github"]
    title: str
    kind: str = ""  # what the message is about, e.g. "PullRequest" or "Issue"
    context: str = ""  # where it comes from, e.g. the repository
    reason: str = ""  # why it reached the user, e.g. "mention" or "review_requested"
    url: str = ""  # the page a browser shows for it
    updated_at: str  # ISO 8601, as the source has it
    unread: bool = True
    dismissed: bool = False


class InboxState(BaseModel):
    """Contents of `<root>/.bytearmy/inbox.json`, and what the UI shows of the inbox."""

    messages: list[Message] = []  # newest first; dismissed ones are kept, but not shown
    errors: dict[str, str] = {}  # by source: why it could not be asked the last time
    last_checked: str | None = None


class ReadChange(BaseModel):
    unread: bool


class GlobalState(BaseModel):
    """Contents of `<root>/.bytearmy/state.json`."""

    project_order: list[str] = []
    focused_project: str | None = None
    ui: UiState = UiState()


class Theme(BaseModel):
    """A color scheme; one file in `<root>/.bytearmy/themes`."""

    name: str = "light"
    appearance: Literal["light", "dark"] = "light"
    colors: dict[str, str] = {}  # CSS colors by the name of their variable, without `--`

    @field_validator("colors")
    @classmethod
    def _plain(cls, colors: dict[str, str]) -> dict[str, str]:
        # The colors go into style sheets: nothing in them may end a rule or the sheet.
        for name, value in colors.items():
            if not re.fullmatch(r"[a-z0-9-]+", name):
                raise ValueError(f"{name!r} is no color name")
            if re.search(r"[;{}<>\\]", value) or not value.strip():
                raise ValueError(f"{value!r} is no color")
        return colors


class OrchestratorFile(BaseModel):
    """Contents of `<root>/.bytearmy/orchestrator.json`."""

    session: str = ""  # its Claude session; it resumes it after a restart
    rows: int = 24
    cols: int = 80
    exit_code: int | None = None  # set when its program ended with an error


class OrchestratorState(BaseModel):
    """What the window shows of the orchestrator."""

    name: str = "orchestrator"
    running: bool = False
    exit_code: int | None = None


class AppState(BaseModel):
    """Everything the UI shows, in one piece."""

    root: str
    projects: list[Project]
    focused_project: str | None
    ui: UiState
    tools: list[Tool] = []
    theme: Theme = Theme()
    inbox: InboxState = InboxState()
    orchestrator: OrchestratorState = OrchestratorState()
    config_errors: list[str] = []  # what is wrong with the config files, for the user to fix


class ProjectOrder(BaseModel):
    order: list[str]


class TaskOrder(BaseModel):
    bar: Literal["backlog", "tasks"]
    order: list[str]


class Focus(BaseModel):
    slug: str | None


class PaneOpen(BaseModel):
    tool: str
    values: dict[str, str] = {}
    beside: str | None = None  # the pane to split; the last pane if not given
    hidden: bool = False  # open it without a place in the layout


class Layout(BaseModel):
    tree: Split | str | None


class Page(BaseModel):
    """What a web pane shows."""

    url: str


class PageVersion(BaseModel):
    """Tells a web pane when to load its page again."""

    version: str | None  # changes with the files the page shows; None if it shows none


class Highlight(BaseModel):
    on: bool


class Unfold(BaseModel):
    open: bool


class Visibility(BaseModel):
    hidden: bool
    beside: str | None = None  # where a shown pane goes; the last pane if not given


# -- what the `bytearmy` command in a pane sends and gets ---------------------


class PaneCall(BaseModel):
    """Open a tool in a new pane; `args` are the values of its parameters.

    An arg `name=value` sets the parameter `name`; the others fill the remaining
    parameters in their order.
    """

    tool: str
    args: list[str] = []
    beside: str | None = None  # the pane to split; the calling pane if not given
    hidden: bool = False  # open it without a place in the layout


class PaneMove(BaseModel):
    beside: str  # the pane the moved one is placed next to


class TaskChange(BaseModel):
    """What an agent may change about its task. Only the fields that are sent change;
    `color` "" removes the color, `reminder` null removes the reminder."""

    status: str = ""
    color: Color = ""
    description: str = ""
    reminder: date | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a status must not be empty")
        if value.strip() == BACKLOG:
            raise ValueError("a started task cannot go back to the backlog")
        return value.strip()


class AgentSpawn(BaseModel):
    role: str  # becomes part of the agent's name: `<project>-<task>-<role>`
    prompt: str  # what the new agent is asked to do
    beside: str | None = None  # the pane to split; the calling pane if not given

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value) or len(value) > 32:
            raise ValueError(
                "a role is up to 32 lower-case letters, digits and single dashes, like reviewer"
            )
        return value

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a prompt must be provided")
        return value


class Agent(BaseModel):
    """An agent of a task, as `bytearmy agent list` shows it."""

    name: str
    pane: str
    master: bool
    running: bool  # its program runs; false after it ended with an error


class ReviewRequest(BaseModel):
    """An agent asks the user to review the changes of the task's clone."""

    mode: DiffMode = "branch"
    note: str = ""  # what the agent wants the user to look at


class PullRequestCreate(BaseModel):
    """An agent opens the pull request of the task's branch."""

    title: str = Field(min_length=1)
    body: str = ""
    base: str = ""  # "" = the repository's default branch
    draft: bool = False


class PullRequestMerge(BaseModel):
    """An agent merges the pull request of the task's branch."""

    method: Literal["merge", "squash", "rebase"] = "squash"
    delete_branch: bool = False


class PullRequestAnswer(BaseModel):
    """What gh answered: a URL, the pull request, or what it did."""

    text: str


class ReviewState(BaseModel):
    """Whether the user has finished a review an agent asked for, and what they said."""

    done: bool
    text: str = ""


class Opened(BaseModel):
    """The answer to a call that opened a pane."""

    pane: str
    agent: str = ""
    logged: bool = False  # the tool added an entry to the task's log


# -- what the `bytearmy` command of the orchestrator sends and gets ------------


class TaskStart(BaseModel):
    """Start a task; with a prompt, a master agent starts in it and gets the prompt."""

    prompt: str | None = None


class TaskSummary(BaseModel):
    """A task as the orchestrator sees it."""

    slug: str
    name: str
    status: str
    color: Color = ""
    branch: str = ""
    description: str = ""
    path: str
    master: str = ""  # the name of its master agent; empty if it has none
    master_running: bool = False


class ProjectSummary(BaseModel):
    """A project with its tasks, as the orchestrator sees it."""

    slug: str
    name: str
    description: str = ""
    github_url: str = ""
    path: str
    backlog: list[TaskSummary]
    tasks: list[TaskSummary]
