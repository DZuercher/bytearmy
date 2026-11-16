"""Checks for `bytearmy workflow`: the GitHub Actions workflows of a task's repository, with a
stand-in for gh."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_agents import BOT, KEEP_ENV, WAIT, calling_pane, configure, key_of, output_until, tool
from test_api_panes import serving
from test_prs import cloned_task

from bytearmy.config import AgentSettings, MasterSettings
from bytearmy.models import PaneOpen
from bytearmy.store import Store
from bytearmy.workflows import WorkflowCommands
from bytearmy.workspaces import Workspaces

# Writes its arguments and directory to gh-calls.txt beside itself, answers like gh would.
FAKE_GH = """\
import json, os, sys
from pathlib import Path
log = Path(__file__).with_name("gh-calls.txt")
with log.open("a") as file:
    file.write(json.dumps(sys.argv[1:]) + " @ " + os.getcwd() + "\\n")
if os.environ.get("FAKE_GH_FAILS"):
    sys.exit("could not find any workflows named deploy.yml")
if sys.argv[1:3] == ["workflow", "list"]:
    print("CI\\tactive\\t1\\nDeploy\\tactive\\t2")
elif sys.argv[1:3] == ["workflow", "run"]:
    print("https://github.com/owner/widgets/actions/runs/42")
elif sys.argv[1:3] == ["run", "list"]:
    print("completed\\tsuccess\\tFix login\\tCI\\tfix/login\\tpush\\t41")
"""


@pytest.fixture
def gh(tmp_path) -> Path:
    script = tmp_path / "gh" / "fake_gh.py"
    script.parent.mkdir()
    script.write_text(FAKE_GH)
    return script


def gh_calls(gh: Path) -> list[tuple[list[str], str]]:
    log = gh.with_name("gh-calls.txt")
    lines = log.read_text().splitlines() if log.exists() else []
    return [(json.loads(args), where) for args, where in (line.split(" @ ") for line in lines)]


@pytest.fixture
def live(tmp_path, gh):
    store = Store(tmp_path / "root")
    commands = WorkflowCommands(gh=[sys.executable, str(gh)])
    workspaces = Workspaces(store, shell=["/bin/sh"], workflow_commands=commands)

    def run(remote: Path, **fields):
        clone = cloned_task(store, remote, **fields)
        return serving(store, workspaces), clone

    return run


def test_run_dispatches_on_the_tasks_branch_with_the_inputs(live, remote, gh, bytearmy):
    serve, clone = live(remote, branch="fix/login")
    with serve as core:
        me = calling_pane(core)

        said = bytearmy(
            me, "workflow", "run", "deploy.yml", "-f", "env=staging", "--field", "note=a = b"
        )

        assert said == (0, "https://github.com/owner/widgets/actions/runs/42")
        assert gh_calls(gh) == [
            (
                [
                    "workflow",
                    "run",
                    "deploy.yml",
                    "--ref",
                    "fix/login",
                    "--raw-field",
                    "env=staging",
                    "--raw-field",
                    "note=a = b",
                ],
                str(clone),
            )
        ]


def test_inputs_come_from_a_file_and_the_ref_can_be_named(live, remote, gh, bytearmy, tmp_path):
    serve, _ = live(remote, branch="feature")
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"env": "prod", "dry_run": True, "count": 3, "text": "a\nb"}))
    with serve as core:
        me = calling_pane(core)

        code, _ = bytearmy(
            me, "workflow", "run", "Deploy", "--ref", "main", "--inputs-file", str(inputs),
            "-f", "env=staging",
        )  # fmt: skip

        assert code == 0
        assert gh_calls(gh)[0][0] == [
            "workflow",
            "run",
            "Deploy",
            "--ref",
            "main",
            "--raw-field",
            "env=staging",
            "--raw-field",
            "dry_run=true",
            "--raw-field",
            "count=3",
            "--raw-field",
            "text=a\nb",
        ]


def test_a_task_without_a_branch_runs_on_the_default_branch(live, remote, gh, bytearmy):
    serve, _ = live(remote)
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "workflow", "run", "ci.yml")[0] == 0
    assert gh_calls(gh)[0][0] == ["workflow", "run", "ci.yml"]


def test_a_field_needs_a_name_and_a_value(live, remote, gh, bytearmy):
    serve, _ = live(remote, branch="feature")
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "workflow", "run", "ci.yml", "-f", "env") == (
            1,
            "bytearmy: --field 'env' is not of the form name=value",
        )
        assert bytearmy(me, "workflow", "run", "ci.yml", "-f", "=prod")[0] == 1
    assert gh_calls(gh) == []


def test_list_and_runs(live, remote, gh, bytearmy):
    serve, _ = live(remote, branch="fix/login")
    with serve as core:
        me = calling_pane(core)

        assert bytearmy(me, "workflow", "list") == (0, "CI\tactive\t1\nDeploy\tactive\t2")
        code, said = bytearmy(me, "workflow", "runs", "CI", "--branch", "fix/login", "--limit", "3")
        assert (code, said) == (0, "completed\tsuccess\tFix login\tCI\tfix/login\tpush\t41")
        assert bytearmy(me, "workflow", "runs")[0] == 0
        assert [args for args, _ in gh_calls(gh)] == [
            ["workflow", "list", "--all"],
            ["run", "list", "--limit", "3", "--workflow", "CI", "--branch", "fix/login"],
            ["run", "list", "--limit", "10"],
        ]


def test_what_gh_says_when_it_fails_reaches_the_agent(live, remote, gh, bytearmy, monkeypatch):
    serve, _ = live(remote, branch="feature")
    monkeypatch.setenv("FAKE_GH_FAILS", "1")
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "workflow", "run", "deploy.yml") == (
            1,
            "bytearmy: `gh workflow run` failed: could not find any workflows named deploy.yml",
        )


def test_the_switch_of_the_master_forbids_dispatching(live, remote, gh, bytearmy):
    serve, _ = live(remote, branch="feature")
    with serve as core:
        tool(core.store, "bot", **{**BOT, "command": f"{KEEP_ENV}; echo {{permissions}}.; {WAIT}"})
        configure(core.store, "[master]\nallow_workflow_dispatch = false\n")
        core.workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
        said = output_until(core.workspaces.attach("demo", "work", "p1", 24, 80), rb"run \*\)")
        assert "--allowedTools=Bash(bytearmy workflow *)" in said
        assert "--disallowedTools=Bash(gh workflow run *)" in said
        master = key_of(core, "p1")

        code, refused = bytearmy(master, "workflow", "run", "ci.yml")
        assert code == 1 and "`bytearmy workflow run` is not allowed to you" in refused
        assert bytearmy(master, "workflow", "list")[0] == 0
    assert [args for args, _ in gh_calls(gh)] == [["workflow", "list", "--all"]]


def test_only_the_master_may_dispatch():
    commands = ["workflow.list", "workflow.run", "workflow.runs"]
    assert AgentSettings().denied(commands) == ["workflow.run"]
    assert MasterSettings().denied(commands) == []
    assert "Bash(bytearmy workflow *)" in AgentSettings().granted_tools()
    assert MasterSettings(allow=["workflow.*"]).granted_tools() == ["Bash(bytearmy workflow *)"]
