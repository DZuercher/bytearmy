"""Checks for `bytearmy pr`: the pull request of a task's branch, with a stand-in for gh."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from conftest import git
from test_agents import (
    BOT,
    KEEP_ENV,
    WAIT,
    calling_pane,
    configure,
    key_of,
    output_until,
    tool,
)
from test_api_panes import serving

from bytearmy.clones import Cloner
from bytearmy.config import AgentSettings, MasterSettings
from bytearmy.models import PaneOpen, ProjectMeta, TaskCreate
from bytearmy.prs import PullRequestCommands
from bytearmy.store import Store
from bytearmy.workspaces import Workspaces

# Writes its arguments and directory to gh-calls.txt beside itself, answers like gh would.
FAKE_GH = """\
import os, sys
from pathlib import Path
log = Path(__file__).with_name("gh-calls.txt")
with log.open("a") as file:
    file.write(" ".join(sys.argv[1:]) + " @ " + os.getcwd() + "\\n")
if os.environ.get("FAKE_GH_FAILS"):
    sys.exit("no pull requests found for branch")
if sys.argv[1:3] == ["pr", "create"]:
    print("https://github.com/owner/widgets/pull/7")
elif sys.argv[1:3] == ["pr", "view"]:
    print("title:\\tAdd widgets\\nstate:\\tOPEN")
"""


@pytest.fixture
def gh(tmp_path) -> Path:
    script = tmp_path / "gh" / "fake_gh.py"
    script.parent.mkdir()
    script.write_text(FAKE_GH)
    return script


def gh_calls(gh: Path) -> list[str]:
    log = gh.with_name("gh-calls.txt")
    return log.read_text().splitlines() if log.exists() else []


def cloned_task(store: Store, remote: Path, **fields) -> Path:
    """Project `demo` cloning `remote`, with the started task `work`; gives its clone."""
    store.create_project(ProjectMeta(name="Demo", github_url=str(remote)))
    task = store.create_task("demo", TaskCreate(name="Work", **fields))
    Cloner(store).start("demo", task, str(remote))
    deadline = time.monotonic() + 20
    while (task := store.task("demo", "work")).clone == "cloning":
        assert time.monotonic() < deadline, "the clone did not end"
        time.sleep(0.02)
    assert task.clone == "ready"
    store.start_task("demo", "work")
    return Path(task.path) / task.repo


@pytest.fixture
def live(tmp_path, gh):
    store = Store(tmp_path / "root")
    commands = PullRequestCommands(gh=[sys.executable, str(gh)])
    workspaces = Workspaces(store, shell=["/bin/sh"], pr_commands=commands)

    def run(remote: Path, **fields):
        clone = cloned_task(store, remote, **fields)
        return serving(store, workspaces), clone

    return run


def test_create_pushes_the_tasks_branch_and_opens_its_pull_request(live, remote, gh, bytearmy):
    serve, clone = live(remote, branch="fix/login")
    (clone / "login.txt").write_text("fixed\n")
    git("add", ".", cwd=clone)
    git("commit", "-m", "fix login", cwd=clone)
    with serve as core:
        me = calling_pane(core)

        code, said = bytearmy(me, "pr", "create", "--title", "Fix login", "--body", "Why.")

        assert (code, said) == (0, "https://github.com/owner/widgets/pull/7")
        assert git("log", "-1", "--format=%s", "fix/login", cwd=remote) == "fix login"
        assert gh_calls(gh) == [
            f"pr create --head fix/login --title Fix login --body Why. @ {clone}"
        ]


def test_view_and_merge_act_on_the_tasks_branch_only(live, remote, gh, bytearmy):
    serve, clone = live(remote, branch="feature")
    with serve as core:
        me = calling_pane(core)

        assert bytearmy(me, "pr", "view") == (0, "title:\tAdd widgets\nstate:\tOPEN")
        assert bytearmy(me, "pr", "merge", "--method", "rebase", "--delete-branch") == (
            0,
            "merged the pull request of feature",
        )
        assert gh_calls(gh) == [
            f"pr view feature @ {clone}",
            f"pr merge feature --rebase --delete-branch @ {clone}",
        ]


def test_what_gh_says_when_it_fails_reaches_the_agent(live, remote, gh, bytearmy, monkeypatch):
    serve, _ = live(remote, branch="feature")
    monkeypatch.setenv("FAKE_GH_FAILS", "1")
    with serve as core:
        me = calling_pane(core)
        code, said = bytearmy(me, "pr", "view")
    assert code == 1
    assert said == "bytearmy: `gh pr view` failed: no pull requests found for branch"


def test_a_task_without_a_branch_has_no_pull_request(live, remote, gh, bytearmy):
    serve, _ = live(remote)
    with serve as core:
        me = calling_pane(core)
        assert bytearmy(me, "pr", "view") == (
            1,
            "bytearmy: the task has no branch, so it can have no pull request",
        )
    assert gh_calls(gh) == []


def test_the_switches_of_the_master_forbid_the_pr_commands(live, remote, gh, bytearmy):
    serve, _ = live(remote, branch="feature")
    with serve as core:
        tool(core.store, "bot", **{**BOT, "command": f"{KEEP_ENV}; echo {{permissions}}.; {WAIT}"})
        configure(core.store, "[master]\nallow_pr_merge = false\n")
        core.workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
        said = output_until(core.workspaces.attach("demo", "work", "p1", 24, 80), rb"merge \*\)")
        assert "--allowedTools=Bash(bytearmy pr *)" in said
        master = key_of(core, "p1")

        code, refused = bytearmy(master, "pr", "merge")
        assert code == 1 and "`bytearmy pr merge` is not allowed to you" in refused
        assert bytearmy(master, "pr", "view")[0] == 0
    assert gh_calls(gh) == [
        f"pr view feature @ {Path(core.store.task('demo', 'work').path) / 'widgets'}"
    ]


def test_only_the_master_may_create_and_merge():
    assert AgentSettings().denied(["pr.create", "pr.view", "pr.merge"]) == ["pr.create", "pr.merge"]
    assert MasterSettings().denied(["pr.create", "pr.view", "pr.merge"]) == []
    assert MasterSettings(allow_pr_creation=False).denied(["pr.create"]) == ["pr.create"]
    # The rule that lets Claude run them goes to every agent that may run one of them.
    assert AgentSettings().granted_tools() == ["Bash(bytearmy pr *)"]
    assert MasterSettings(allow=["pane.*"]).granted_tools() == []
