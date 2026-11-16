"""Checks for the settings: the root's config.toml and those of projects and tasks."""

from __future__ import annotations

import json
import tomllib

import pytest

from bytearmy.config import (
    CONFIG_FILE,
    DEFAULT_TEXT,
    ClaudeSettings,
    Config,
    GitHubAccounts,
    GitHubSettings,
    MasterSettings,
    OrchestratorSettings,
    ToolSettings,
    ensure_config,
    load_config,
    merge,
)
from bytearmy.inbox import GitHubSource
from bytearmy.models import Message
from bytearmy.themes import chosen
from bytearmy.tools import ensure_premade, load_tools


def write(directory, text: str) -> None:
    (directory / CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
    (directory / CONFIG_FILE).write_text(text)


def test_the_default_file_says_what_the_defaults_are():
    assert Config.model_validate(tomllib.loads(DEFAULT_TEXT)) == Config()


def test_a_missing_root_config_is_written_and_an_existing_one_kept(tmp_path):
    ensure_config(tmp_path)
    assert (tmp_path / CONFIG_FILE).read_text() == DEFAULT_TEXT

    write(tmp_path, 'color_scheme = "dark"\n')
    ensure_config(tmp_path)
    assert (tmp_path / CONFIG_FILE).read_text() == 'color_scheme = "dark"\n'
    # A file from before the settings, with only the color scheme, still holds.
    assert load_config(tmp_path) == (Config(color_scheme="dark"), [])
    assert chosen(tmp_path) == "dark"


def test_without_any_file_the_defaults_hold(tmp_path):
    assert load_config(tmp_path, tmp_path / "p", tmp_path / "p" / "t") == (Config(), [])


def test_tables_merge_and_other_values_are_replaced():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    assert merge(base, {"a": {"y": [3]}, "c": 2}) == {"a": {"x": 1, "y": [3]}, "b": 1, "c": 2}
    assert base == {"a": {"x": 1, "y": [1, 2]}, "b": 1}


def test_a_task_overrides_its_project_which_overrides_the_root(tmp_path):
    project, task = tmp_path / "p", tmp_path / "p" / "t"
    write(tmp_path, '[master]\nallow = ["*"]\npermission_mode = "plan"\n[github]\nuser = "me"\n')
    write(project, '[master]\nallow = ["pane.*"]\n[tools.vim]\nenabled = false\n')
    write(task, '[github]\nuser = "work"\n[tools.vim]\ncommand = "nvim {file}"\n')

    root_config, _ = load_config(tmp_path)
    project_config, _ = load_config(tmp_path, project)
    task_config, errors = load_config(tmp_path, project, task)

    assert errors == []
    assert root_config.master.allow == ["*"]
    assert project_config.master.allow == ["pane.*"]
    assert project_config.master.permission_mode == "plan"  # kept from the root
    assert project_config.github.user == "me"
    assert task_config.github.user == "work"
    assert task_config.tools["vim"] == ToolSettings(enabled=False, command="nvim {file}")


def test_what_belongs_to_no_project_counts_only_at_the_root(tmp_path):
    project = tmp_path / "p"
    write(
        project, 'color_scheme = "dark"\n[orchestrator]\nautostart = false\n[agents]\nallow = []\n'
    )

    config, errors = load_config(tmp_path, project)

    assert config.color_scheme == "light"
    assert config.orchestrator.autostart is True
    assert config.agents.allow == []
    assert len(errors) == 2 and "color_scheme counts only" in errors[0]


@pytest.mark.parametrize(
    "text",
    ["[master\n", "colour_scheme = 'dark'\n", "[inbox]\npoll_seconds = 5\n"],
)
def test_a_file_that_cannot_be_used_is_skipped_and_reported(tmp_path, text):
    write(tmp_path, '[github]\nuser = "me"\n')
    write(tmp_path / "p", text)

    config, errors = load_config(tmp_path, tmp_path / "p")

    assert config.github.user == "me"  # the root's file still holds
    assert len(errors) == 1 and str(tmp_path / "p") in errors[0]


def test_allow_takes_patterns_and_the_flags_are_quoted():
    settings = ClaudeSettings(
        allow=["pane.*", "log"],
        permission_mode="acceptEdits",
        allowed_tools=["Bash(git *)", "Read"],
        disallowed_tools=["WebFetch"],
    )
    assert settings.allows("pane.open") and settings.allows("log")
    assert not settings.allows("agent.spawn")
    assert settings.denied(["pane.list", "review"]) == ["review"]
    assert settings.flags() == (
        "--permission-mode acceptEdits '--allowedTools=Bash(git *)' --allowedTools=Read"
        " --disallowedTools=WebFetch"
    )
    assert ClaudeSettings().flags() == ""


def test_the_allow_switches_forbid_whatever_allow_says():
    orchestrator = OrchestratorSettings(
        allow=["*"], allow_task_creation=False, allow_task_deletion=False
    )
    assert orchestrator.denied(["tasks.create", "tasks.delete", "tasks.start"]) == [
        "tasks.create",
        "tasks.delete",
    ]
    assert OrchestratorSettings().denied(["tasks.create", "tasks.delete"]) == []

    master = MasterSettings(
        allow_pr_creation=False, allow_pr_merge=False, allow_workflow_dispatch=False
    )
    assert master.flags() == (
        "'--allowedTools=Bash(bytearmy pr *)' '--allowedTools=Bash(bytearmy workflow *)'"
        " '--disallowedTools=Bash(gh pr create *)' '--disallowedTools=Bash(gh pr merge *)'"
        " '--disallowedTools=Bash(gh workflow run *)'"
    )
    assert len(master.forbidden_actions()) == 3
    assert master.denied(["workflow.list", "workflow.run"]) == ["workflow.run"]
    assert MasterSettings().flags() == (
        "'--allowedTools=Bash(bytearmy pr *)' '--allowedTools=Bash(bytearmy workflow *)'"
    )
    assert MasterSettings().forbidden_actions() == []
    assert MasterSettings(allow_pr_merge=False).forbidden_tools() == ["Bash(gh pr merge *)"]


def test_the_tools_section_leaves_out_and_changes_tools(tmp_path):
    ensure_premade(tmp_path)
    settings = {
        "pycharm": ToolSettings(enabled=False),
        "vim": ToolSettings(command="nvim {file}", callers=["user"]),
    }

    tools = load_tools(tmp_path, settings=settings)

    assert "pycharm" not in tools
    assert (tools["vim"].command, tools["vim"].callers) == ("nvim {file}", ["user"])
    assert load_tools(tmp_path)["vim"].command == "vim {file}"


def test_a_github_account_becomes_gh_and_git_credentials():
    asked = []

    def gh(command):
        asked.append(command)
        if command[-1] == "unknown":
            raise RuntimeError("no account")
        return "secret\n"

    accounts = GitHubAccounts(run=gh)
    assert accounts.env(GitHubSettings()) == {}  # gh's active account
    env = accounts.env(GitHubSettings(user="me", host="ghe.example.com"))
    assert env["GH_TOKEN"] == "secret" and env["GH_HOST"] == "ghe.example.com"
    assert env["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"
    assert asked == [["gh", "auth", "token", "--hostname", "ghe.example.com", "--user", "me"]]
    accounts.env(GitHubSettings(user="me", host="ghe.example.com"))
    assert len(asked) == 1  # the token is kept for a while

    assert accounts.env(GitHubSettings(user="unknown")) == {}
    assert "unknown" in accounts.problems()[0]


def test_the_github_section_sets_the_git_identity_of_commits():
    accounts = GitHubAccounts(run=lambda _command: "secret\n")
    env = accounts.env(GitHubSettings(name="Ada Lovelace", email="ada@example.com"))
    assert env == {  # no account: no token, but still the identity
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "Ada Lovelace",
        "GIT_CONFIG_KEY_1": "user.email",
        "GIT_CONFIG_VALUE_1": "ada@example.com",
    }
    env = accounts.env(GitHubSettings(user="me", email="me@example.com"))
    assert env["GH_TOKEN"] == "secret" and env["GIT_CONFIG_COUNT"] == "3"
    assert (env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]) == ("user.email", "me@example.com")


def test_the_inbox_takes_only_the_notifications_the_config_wants():
    config = Config.model_validate(
        {"inbox": {"github": {"reasons": ["review_requested"], "repositories": ["o/a"]}}}
    )
    threads = [
        {"id": "1", "reason": "review_requested", "repository": {"full_name": "o/a"}},
        {"id": "2", "reason": "subscribed", "repository": {"full_name": "o/a"}},
        {"id": "3", "reason": "review_requested", "repository": {"full_name": "o/b"}},
    ]
    source = GitHubSource(run=lambda _command: json.dumps(threads), settings=lambda: config)
    messages, _complete = source.fetch()
    assert [message.id for message in messages] == ["github:1"]
    assert isinstance(messages[0], Message)
