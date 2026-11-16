"""The settings of bytearmy: one `config.toml` at the root, optionally one per project and task.

    <root>/.bytearmy/config.toml                  always there; written from the defaults
    <project>/.bytearmy/config.toml               optional; overrides the root's
    <project>/<task>/.bytearmy/config.toml        optional; overrides the project's

Tables merge key by key, anything else is replaced by the lower file. What belongs to no
project (the color scheme, the orchestrator and the inbox) is only read from the root's file.
The files are read again whenever a setting is needed, so an edit counts from then on.
"""

from __future__ import annotations

import fnmatch
import logging
import shlex
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

CONFIG_FILE = Path(".bytearmy") / "config.toml"  # below the root, a project or a task
ROOT_ONLY = ("color_scheme", "orchestrator", "inbox")
MIN_POLL_SECONDS = 60.0  # GitHub asks clients to ask for notifications no more than once a minute
TOKEN_SECONDS = 300.0  # how long a token gh gave is used before gh is asked again

# The commands of the `bytearmy` command line tool, as `allow` names them.
ORCHESTRATOR_COMMANDS = (
    "project.list",
    "project.create",
    "project.delete",
    "tasks.list",
    "tasks.create",
    "tasks.start",
    "tasks.delete",
    "inbox.list",
    "inbox.read",
    "inbox.dismiss",
)
AGENT_COMMANDS = (
    "pane.list",
    "pane.open",
    "pane.close",
    "pane.maximize",
    "pane.hide",
    "pane.highlight",
    "pane.move",
    "tool.list",
    "task.show",
    "task.set-status",
    "task.set-color",
    "task.set-description",
    "task.set-reminder",
    "agent.list",
    "agent.spawn",
    "review",
    "log",
    "pr.create",
    "pr.view",
    "pr.merge",
)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeSettings(Section):
    """How an agent is started and what it may do."""

    command: str = ""  # "" = the claude tool's
    resume: str = ""
    allow: list[str] = []
    permission_mode: str = ""
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []

    def switched_off(self) -> set[str]:
        """The `bytearmy` commands an `allow_*` switch forbids, whatever `allow` says."""
        return set()

    def forbidden_tools(self) -> list[str]:
        """Claude tool rules an `allow_*` switch forbids, on top of `disallowed_tools`."""
        return []

    def forbidden_actions(self) -> list[str]:
        """What the `allow_*` switches forbid beyond `bytearmy` commands, for the
        agent's instructions."""
        return []

    def allows(self, command: str) -> bool:
        return command not in self.switched_off() and any(
            fnmatch.fnmatchcase(command, pattern) for pattern in self.allow
        )

    def denied(self, commands: Sequence[str]) -> list[str]:
        return [command for command in commands if not self.allows(command)]

    def granted_tools(self) -> list[str]:
        """Claude tool rules bytearmy adds to `allowed_tools`."""
        return []

    def flags(self) -> str:
        """Claude's own permission flags, for {permissions} in a command.

        Each rule is one `--flag=rule`: the flags take several values, and a prompt
        after `--flag rule` would be taken for one more rule.
        """
        parts: list[str] = []
        if self.permission_mode:
            parts += ["--permission-mode", self.permission_mode]
        allowed = [*self.allowed_tools, *self.granted_tools()]
        parts += [f"--allowedTools={rule}" for rule in allowed]
        disallowed = [*self.disallowed_tools, *self.forbidden_tools()]
        parts += [f"--disallowedTools={rule}" for rule in disallowed]
        return shlex.join(parts)


class OrchestratorSettings(ClaudeSettings):
    autostart: bool = True
    allow: list[str] = ["project.*", "tasks.*", "inbox.*"]
    allow_task_creation: bool = True
    allow_task_deletion: bool = True
    announce_inbox: bool = True
    quiet_seconds: float = Field(30.0, ge=0)

    def switched_off(self) -> set[str]:
        off: set[str] = set()
        if not self.allow_task_creation:
            off.add("tasks.create")
        if not self.allow_task_deletion:
            off.add("tasks.delete")
        return off


# The `bytearmy pr` commands, and the rule that lets Claude run them in any permission mode.
PR_COMMANDS = ("pr.create", "pr.view", "pr.merge")
PR_COMMAND_RULE = "Bash(bytearmy pr *)"


class TaskAgentSettings(ClaudeSettings):
    spawn: str = ""  # "" = the claude tool's
    allow: list[str] = ["*"]

    def granted_tools(self) -> list[str]:
        return [PR_COMMAND_RULE] if any(self.allows(command) for command in PR_COMMANDS) else []


# The gh commands the pull request switches of [master] keep from Claude, as tool rules.
PR_CREATION_RULES = ("Bash(gh pr create *)",)
PR_MERGE_RULES = ("Bash(gh pr merge *)",)


class MasterSettings(TaskAgentSettings):
    allow_pr_creation: bool = True
    allow_pr_merge: bool = True

    def switched_off(self) -> set[str]:
        off: set[str] = set()
        if not self.allow_pr_creation:
            off.add("pr.create")
        if not self.allow_pr_merge:
            off.add("pr.merge")
        return off

    def forbidden_tools(self) -> list[str]:
        rules: list[str] = []
        if not self.allow_pr_creation:
            rules += PR_CREATION_RULES
        if not self.allow_pr_merge:
            rules += PR_MERGE_RULES
        return rules

    def forbidden_actions(self) -> list[str]:
        actions: list[str] = []
        if not self.allow_pr_creation:
            actions.append(
                "create pull requests (with `bytearmy pr create`, `gh pr create` or any other way)"
            )
        if not self.allow_pr_merge:
            actions.append(
                "merge pull requests (with `bytearmy pr merge`, `gh pr merge` or any other way)"
            )
        return actions


class AgentSettings(TaskAgentSettings):
    def switched_off(self) -> set[str]:
        """Creating and merging the pull request is the master's: its switches rule it."""
        return {"pr.create", "pr.merge"}


class ToolSettings(Section):
    """Overrides of the fields of a tool file."""

    enabled: bool = True
    name: str | None = None
    callers: list[Literal["user", "agent"]] | None = None
    command: str | None = None
    resume: str | None = None
    spawn: str | None = None
    url: str | None = None
    directory: Literal["task", "repo"] | None = None

    def fields(self) -> dict[str, Any]:
        return self.model_dump(exclude={"enabled"}, exclude_none=True)


class GitHubInbox(Section):
    enabled: bool = True
    reasons: list[str] = []
    repositories: list[str] = []

    def wants(self, reason: str, repository: str) -> bool:
        return (not self.reasons or reason in self.reasons) and (
            not self.repositories or repository in self.repositories
        )


class InboxSettings(Section):
    enabled: bool = True
    poll_seconds: float = Field(60.0, ge=MIN_POLL_SECONDS)
    github: GitHubInbox = GitHubInbox()


class GitHubSettings(Section):
    user: str = ""
    host: str = "github.com"
    name: str = ""
    email: str = ""


class Config(Section):
    color_scheme: str = "light"
    orchestrator: OrchestratorSettings = OrchestratorSettings()
    master: MasterSettings = MasterSettings()
    agents: AgentSettings = AgentSettings()
    tools: dict[str, ToolSettings] = {}
    inbox: InboxSettings = InboxSettings()
    github: GitHubSettings = GitHubSettings()


DEFAULT_TEXT = """\
# The settings of bytearmy. This file holds those of all projects and tasks.
# <project>/.bytearmy/config.toml and <project>/<task>/.bytearmy/config.toml may set any of
# them again for one project or task; tables merge key by key, other values are replaced.
# color_scheme, [orchestrator] and [inbox] belong to no project: they count only here.
# Edits count from then on; agents that run already keep how they were started.

# The colors of the window: a file in .bytearmy/themes, without .toml.
color_scheme = "light"

# The one agent above all tasks.
[orchestrator]
# Start it with bytearmy; otherwise it starts when its tab is first shown.
autostart = true
# Its command, and the one that resumes it after a restart; "" takes those of the claude tool.
# {session}, {agent}, {instructions} and {permissions} are filled in.
command = ""
resume = ""
# The `bytearmy` commands it may call, as patterns: project.list, project.create,
# project.delete, tasks.list, tasks.create, tasks.start, tasks.delete, inbox.list,
# inbox.read, inbox.dismiss. E.g. ["project.list", "tasks.*", "inbox.*"].
allow = ["project.*", "tasks.*", "inbox.*"]
# Whether it may create and delete tasks; false forbids tasks.create or tasks.delete,
# whatever allow says.
allow_task_creation = true
allow_task_deletion = true
# Claude's own permissions, passed as {permissions}: --permission-mode (default,
# acceptEdits, plan, bypassPermissions), --allowedTools and --disallowedTools.
permission_mode = ""
allowed_tools = []
disallowed_tools = []
# Type a note into its terminal when new messages reach the inbox ...
announce_inbox = true
# ... once the user has typed nothing into it for this many seconds.
quiet_seconds = 30

# The master agent of a task: the first agent in it.
[master]
# "" takes the command, resume and spawn line of the claude tool. spawn is used when it
# starts with a prompt (e.g. from the orchestrator), and may hold {prompt}.
command = ""
resume = ""
spawn = ""
# The `bytearmy` commands it may call, as patterns: pane.list, pane.open, pane.close,
# pane.maximize, pane.hide, pane.highlight, pane.move, tool.list, task.show,
# task.set-status, task.set-color, task.set-description, task.set-reminder, agent.list,
# agent.spawn, review, log, pr.create, pr.view, pr.merge.
allow = ["*"]
# Whether it may create and merge the pull request of the task's branch. `bytearmy pr`
# does it with gh as the account of [github], for the task's branch only, and Claude is
# let run it in every permission mode, auto mode too. false forbids `bytearmy pr create`
# or `bytearmy pr merge`, adds `gh pr create` or `gh pr merge` to the tools Claude may not
# use, and tells the agent not to.
allow_pr_creation = true
allow_pr_merge = true
permission_mode = ""
allowed_tools = []
disallowed_tools = []

# Every other agent of a task: the ones agents spawn and the ones the user opens. They may
# not create or merge the pull request (pr.create, pr.merge), whatever allow says.
[agents]
command = ""
resume = ""
spawn = ""
allow = ["*"]
permission_mode = ""
allowed_tools = []
disallowed_tools = []

# Settings of single tools, by the name of their file in .tools. A tool that is not
# enabled is not offered. name, callers, command, resume, spawn, url and directory replace
# what the tool file says. E.g.
# [tools.pycharm]
# enabled = false
# [tools.vim]
# command = "nvim {file}"

# Messages for the user from outside bytearmy.
[inbox]
enabled = true
# How often the sources are asked; at least 60.
poll_seconds = 60

[inbox.github]
enabled = true
# Only notifications for these reasons (review_requested, assign, mention, author,
# comment, ci_activity, state_change, subscribed, team_mention, ...); [] takes all.
reasons = []
# Only notifications of these repositories, as "owner/name"; [] takes all.
repositories = []

# The GitHub account of gh and git: for the inbox, for clones and in the panes.
[github]
# One of the accounts `gh auth status` lists; "" takes gh's active account.
user = ""
host = "github.com"
# The name and email git puts on commits (user.name and user.email); "" keeps git's own.
name = ""
email = ""
"""


def ensure_config(root: Path) -> None:
    """Write the root's config from the defaults if it has none."""
    file = root / CONFIG_FILE
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(DEFAULT_TEXT)


def merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """`base` with `over` laid on it: tables merge key by key, anything else is replaced."""
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _layer(file: Path, root_level: bool, errors: list[str]) -> dict[str, Any]:
    """The settings of one file; nothing if it is missing or cannot be read."""
    if not file.is_file():
        return {}
    try:
        data = tomllib.loads(file.read_text())
        Config.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{file} cannot be read: {error}")
        return {}
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors()
        )
        errors.append(f"{file} is ignored: {problems}")
        return {}
    if not root_level:
        for key in ROOT_ONLY:
            if key in data:
                errors.append(f"{file}: {key} counts only in the root's config.toml")
                del data[key]
    return data


_logged: set[str] = set()
_logged_lock = threading.Lock()


def load_config(
    root: Path, project: Path | None = None, task: Path | None = None
) -> tuple[Config, list[str]]:
    """The settings that hold in a task, a project or (without either) the whole root,
    and what was wrong with the files."""
    errors: list[str] = []
    merged = _layer(root / CONFIG_FILE, True, errors)
    for directory in (project, task):
        if directory is not None:
            merged = merge(merged, _layer(directory / CONFIG_FILE, False, errors))
    try:
        config = Config.model_validate(merged)
    except ValidationError as error:  # each layer was valid, together they are not
        errors.append(f"The config files do not fit together, the defaults hold: {error}")
        config = Config()
    with _logged_lock:
        for error in errors:
            if error not in _logged:  # the files are read often: say each problem once
                _logged.add(error)
                log.warning("%s", error)
    return config, errors


def config_of(root: Path, project: Path | None = None, task: Path | None = None) -> Config:
    return load_config(root, project, task)[0]


# -- the GitHub account ---------------------------------------------------------

Runner = Callable[[Sequence[str]], str]


def run_gh(command: Sequence[str]) -> str:
    done = subprocess.run(list(command), capture_output=True, text=True, timeout=20, check=False)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:300] or "gh failed")
    return done.stdout


class GitHubAccounts:
    """The environment that makes gh and git act as the account the config names."""

    def __init__(
        self,
        run: Runner = run_gh,
        clock: Callable[[], float] = time.monotonic,
        max_age: float = TOKEN_SECONDS,
    ) -> None:
        self._run = run
        self._clock = clock
        self._max_age = max_age
        self._lock = threading.Lock()
        self._tokens: dict[tuple[str, str], tuple[float, str]] = {}
        self.errors: dict[tuple[str, str], str] = {}  # accounts gh did not know, and why

    def env(self, settings: GitHubSettings) -> dict[str, str]:
        """GH_TOKEN and git's credential helper for the account, and git's user.name and
        user.email for commits. Without an account, or one gh does not know, gh's active
        account is used; without a name or email, git's own."""
        token = self._token(settings) if settings.user else ""
        git_config: list[tuple[str, str]] = []
        env: dict[str, str] = {}
        if token:
            env = {"GH_TOKEN": token, "GH_HOST": settings.host}
            # git asks gh for credentials, and gh answers with GH_TOKEN.
            helper = f"credential.https://{settings.host}.helper"
            git_config += [(helper, ""), (helper, "!gh auth git-credential")]
        if settings.name:
            git_config.append(("user.name", settings.name))
        if settings.email:
            git_config.append(("user.email", settings.email))
        if git_config:
            env["GIT_CONFIG_COUNT"] = str(len(git_config))
            for index, (key, value) in enumerate(git_config):
                env[f"GIT_CONFIG_KEY_{index}"] = key
                env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    def _token(self, settings: GitHubSettings) -> str:
        """The account's token from gh, kept for a while; "" if gh does not know it."""
        account = (settings.host, settings.user)
        with self._lock:
            checked, token = self._tokens.get(account, (None, ""))
            fresh = checked is not None and self._clock() - checked < self._max_age
        if not fresh:
            command = ["gh", "auth", "token", "--hostname", settings.host, "--user", settings.user]
            try:
                token = self._run(command).strip()
                error = "" if token else "gh gave no token"
            except (OSError, RuntimeError, subprocess.SubprocessError) as failure:
                token, error = "", str(failure)
            with self._lock:
                self._tokens[account] = (self._clock(), token)
                if error:
                    message = (
                        f"GitHub account {settings.user} on {settings.host} is not known to gh"
                        f" ({error}); gh's active account is used."
                    )
                    if self.errors.get(account) != message:
                        log.warning("%s", message)
                    self.errors[account] = message
                else:
                    self.errors.pop(account, None)
        return token

    def problems(self) -> list[str]:
        with self._lock:
            return list(self.errors.values())


ACCOUNTS = GitHubAccounts()  # shared by everything that runs gh or git


def project_github_env(root: Path, project: Path | None) -> dict[str, str]:
    """The GitHub environment that holds in a project (or the root, without one)."""
    return ACCOUNTS.env(config_of(root, project).github)
