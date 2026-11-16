"""Checks for the diff pane: diffle shows the changes, and the comments the user writes there
reach an agent when the user finishes the review."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import httpx
import pytest
from conftest import git, wait_until
from test_agents import calling_pane, core, tool  # noqa: F401  (core is a fixture)
from test_api_panes import PANES

from bytearmy import cli, reviews
from bytearmy.models import PaneOpen, ProjectMeta, ReviewRequest, TaskCreate
from bytearmy.store import Store
from bytearmy.tools import ToolFailed
from bytearmy.workspaces import Workspaces

pytestmark = pytest.mark.skipif(shutil.which("diffle") is None, reason="diffle is not installed")

RECORDER = 'cat -v > "$BYTEARMY_PANE.got"'  # writes down what is typed into it

# -- what is compared ------------------------------------------------------------


@pytest.fixture
def clone(tmp_path, remote) -> Path:
    """A clone of `remote` on a branch of its own, with a commit and an uncommitted change."""
    work = tmp_path / "clone"
    git("clone", str(remote), str(work), cwd=tmp_path)
    git("switch", "-c", "task", cwd=work)
    (work / "lib.py").write_text("one\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "work", cwd=work)
    (work / "lib.py").write_text("one\ntwo\n")
    return work


def test_the_branch_is_compared_with_the_default_branch(clone):
    assert reviews.comparison(clone, "branch") == ("origin/main", "origin/main")
    assert reviews.comparison(clone, "uncommitted") == ("working", "HEAD")


def test_the_branch_is_compared_with_its_base_branch(clone):
    assert reviews.comparison(clone, "branch", "feature") == ("origin/feature", "origin/feature")
    # A base origin does not know falls back to the default branch.
    assert reviews.comparison(clone, "branch", "gone") == ("origin/main", "origin/main")


def test_without_a_default_branch_the_changes_are_those_since_head(tmp_path):
    git("init", "-b", "work", cwd=tmp_path)
    assert reviews.comparison(tmp_path, "branch") == ("working", "HEAD")  # no commit yet
    (tmp_path / "a.txt").write_text("a\n")
    git("add", ".", cwd=tmp_path)
    git("commit", "-m", "first", cwd=tmp_path)
    assert reviews.comparison(tmp_path, "branch") == ("working", "HEAD")


def test_a_review_reads_as_a_message_to_an_agent():
    text = reviews.format_review("origin/main", "a.txt:1\n\n> one\n\nWhy?\n\n---\n")
    assert text == (
        "Review comments from the user on the changes against origin/main:\n\n"
        "a.txt:1\n\n> one\n\nWhy?\n\n---\n"
    )
    assert "without comments" in reviews.format_review("HEAD", "")


# -- the diff pane ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def workspaces(store):
    made = Workspaces(store, shell=["/bin/sh"])
    yield made
    made.shutdown()


@pytest.fixture
def task(store) -> Path:
    """Started task `demo/work` without a clone; its directory is a repository."""
    store.create_project(ProjectMeta(name="Demo"))
    store.create_task("demo", TaskCreate(name="Work"))
    store.start_task("demo", "work")
    path = Path(store.task("demo", "work").path)
    git("init", "-b", "main", cwd=path)
    (path / ".git" / "info" / "exclude").write_text(".bytearmy/\n*.got\n")
    (path / "a.txt").write_text("one\n")
    git("add", ".", cwd=path)
    git("commit", "-m", "first", cwd=path)
    (path / "a.txt").write_text("one\ntwo\n")
    return path


def comment(url: str, body: str) -> None:
    """Write a comment on line 2 of a.txt, as the user would in diffle."""
    thread = {"path": "a.txt", "startLine": 2, "side": "new", "body": body}
    assert httpx.post(url + "api/threads", json=thread).status_code == 201


def open_threads(url: str) -> list[dict]:
    return httpx.get(url + "api/threads", params={"state": "open"}).json()["threads"]


def test_a_diff_pane_shows_diffle(workspaces, task):
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    assert pane.kind == "diff"
    url = workspaces.page("demo", "work", pane.id, files="")
    assert url.startswith("http://127.0.0.1:")
    assert httpx.get(url + "api/server").json()["label"] == "main...worktree"
    assert workspaces.page("demo", "work", pane.id, files="") == url  # one diffle per pane
    assert workspaces.page_version("demo", "work", pane.id) is None


def test_finishing_the_review_types_it_into_the_master(store, workspaces, task):
    tool(store, "bot", name="Bot", agent=True, command=RECORDER)
    master = workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
    workspaces.start("demo", "work", master.id)
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    url = workspaces.page("demo", "work", pane.id, files="")
    comment(url, "Why two?")
    workspaces.finish_review("demo", "work", pane.id)

    got = task / f"{master.id}.got"
    wait_until(lambda: got.exists() and "Why two?" in got.read_text(), "the master gets it")
    assert got.read_text().startswith("^[[200~Review comments from the user")
    assert "a.txt:2" in got.read_text()
    assert pane.id not in store.task("demo", "work").workspace.panes
    assert store.log("demo", "work")[-1].text == "Finished a review with 1 comment"

    # The comment was sent: the next review starts without it.
    again = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    assert open_threads(workspaces.page("demo", "work", again.id, files="")) == []


def test_closing_the_pane_sends_nothing_and_keeps_the_comments(store, workspaces, task):
    tool(store, "bot", name="Bot", agent=True, command=RECORDER)
    master = workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
    workspaces.start("demo", "work", master.id)
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    comment(workspaces.page("demo", "work", pane.id, files=""), "Not yet.")
    workspaces.close_pane("demo", "work", pane.id)
    assert pane.id not in store.task("demo", "work").workspace.panes

    again = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    threads = open_threads(workspaces.page("demo", "work", again.id, files=""))
    assert [thread["messages"][0]["body"] for thread in threads] == ["Not yet."]
    got = task / f"{master.id}.got"
    assert not got.exists() or "Review comments" not in got.read_text()


def test_without_a_master_the_comments_wait_for_the_next_review(store, workspaces, task):
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    comment(workspaces.page("demo", "work", pane.id, files=""), "Later.")
    workspaces.finish_review("demo", "work", pane.id)
    assert pane.id not in store.task("demo", "work").workspace.panes
    assert "no master agent" in store.log("demo", "work")[-1].text

    again = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    threads = open_threads(workspaces.page("demo", "work", again.id, files=""))
    assert [thread["messages"][0]["body"] for thread in threads] == ["Later."]


def test_without_diffle_the_pane_says_so(workspaces, task, monkeypatch):
    monkeypatch.setattr(reviews, "PROGRAM", "no-such-diffle")
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    with pytest.raises(ToolFailed, match="diffle is not installed"):
        workspaces.page("demo", "work", pane.id, files="")


def test_an_agent_asks_for_a_review_and_gets_it(core, monkeypatch, capsys):  # noqa: F811
    path = Path(core.store.task("demo", "work").path)
    git("init", "-b", "main", cwd=path)
    (path / "a.txt").write_text("one\n")
    git("add", "a.txt", cwd=path)
    git("commit", "-m", "first", cwd=path)
    (path / "a.txt").write_text("one\ntwo\n")
    me = calling_pane(core)
    for name in ("BYTEARMY_URL", "BYTEARMY_TOKEN", "BYTEARMY_PANE"):
        monkeypatch.setenv(name, me[name])
    monkeypatch.setattr(cli, "REVIEW_POLL_SECONDS", 0.05)
    codes = []
    waiting = threading.Thread(
        target=lambda: codes.append(cli.main(["review", "--note", "look at a.txt"]))
    )
    waiting.start()

    def review_pane():
        panes = core.workspace()["panes"].values()
        return next((pane for pane in panes if pane["kind"] == "diff"), None)

    wait_until(review_pane, "the review pane opens")
    pane = review_pane()
    assert pane["highlighted"]
    assert pane["title"] == "Review Changes: look at a.txt"
    assert pane["values"] == {
        "requester": me["BYTEARMY_PANE"],
        "mode": "branch",
        "note": "look at a.txt",
    }
    url = core.http.get(f"{PANES}/{pane['id']}/page").json()["url"]
    comment(url, "Fine.")
    assert core.http.post(f"{PANES}/{pane['id']}/finish").status_code == 200
    waiting.join(timeout=5)

    assert codes == [0]
    out = capsys.readouterr().out
    assert out.startswith(f"opened the review in pane {pane['id']}")
    assert "a.txt:2" in out and "Fine." in out


def test_an_agent_hears_when_the_review_has_no_comments(core):  # noqa: F811
    path = Path(core.store.task("demo", "work").path)
    git("init", "-b", "main", cwd=path)
    me = calling_pane(core)
    caller = ("demo", "work", me["BYTEARMY_PANE"])
    opened = core.workspaces.request_review(caller, ReviewRequest())
    core.http.post(f"{PANES}/{opened.id}/finish")
    state = core.workspaces.review_state(caller, opened.id)
    assert state.done and "finished the review without comments" in state.text


def test_an_agent_hears_when_the_review_is_closed_unsent(core):  # noqa: F811
    path = Path(core.store.task("demo", "work").path)
    git("init", "-b", "main", cwd=path)
    me = calling_pane(core)
    caller = ("demo", "work", me["BYTEARMY_PANE"])
    opened = core.workspaces.request_review(caller, ReviewRequest())
    core.http.delete(f"{PANES}/{opened.id}")
    state = core.workspaces.review_state(caller, opened.id)
    assert state.done and "without sending it" in state.text
