"""Checks for the diff pane: the changes it shows, and the reviews the user writes there."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from conftest import git, wait_until
from test_agents import calling_pane, core, tool  # noqa: F401  (core is a fixture)
from test_api_panes import PANES

from bytearmy import cli, diffs
from bytearmy.models import PaneOpen, ProjectMeta, Review, ReviewComment, TaskCreate
from bytearmy.store import Store
from bytearmy.workspaces import InvalidValues, NoMaster, Workspaces

# -- the changes ---------------------------------------------------------------


@pytest.fixture
def clone(tmp_path, remote) -> Path:
    """A clone of `remote` on a branch of its own with every kind of change.

    Committed: `lib.py` changed, `README.md` renamed to `README.txt`, `binary.bin` added.
    Uncommitted: `lib.py` changed again, `old.txt` deleted, `new.txt` untracked.
    """
    work = tmp_path / "clone"
    git("clone", str(remote), str(work), cwd=tmp_path)
    (work / "lib.py").write_text("".join(f"line {number}\n" for number in range(1, 21)))
    (work / "old.txt").write_text("going\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "base", cwd=work)
    git("push", "origin", "main", cwd=work)
    git("fetch", cwd=work)
    git("switch", "-c", "task", cwd=work)
    lines = (work / "lib.py").read_text().splitlines(keepends=True)
    lines[4] = "line five\n"
    (work / "lib.py").write_text("".join(lines))
    git("mv", "README.md", "README.txt", cwd=work)
    (work / "binary.bin").write_bytes(b"\x00\x01\x02")
    git("add", ".", cwd=work)
    git("commit", "-m", "work", cwd=work)
    lines[14] = "line fifteen\n"
    (work / "lib.py").write_text("".join(lines))
    (work / "old.txt").unlink()
    (work / "new.txt").write_text("brand\nnew\n")
    return work


def by_path(diff) -> dict:
    return {file.path: file for file in diff.files}


def test_the_branch_shows_everything_since_the_default_branch(clone):
    diff = diffs.diff(clone, "branch")
    assert diff.base == "origin/main"
    assert diff.commit == git("rev-parse", "origin/main", cwd=clone)
    files = by_path(diff)
    assert sorted(files) == ["README.txt", "binary.bin", "lib.py", "new.txt", "old.txt"]
    assert (files["README.txt"].status, files["README.txt"].old_path) == ("renamed", "README.md")
    assert files["binary.bin"].binary and files["binary.bin"].status == "added"
    assert files["old.txt"].status == "deleted"
    assert files["new.txt"].status == "added"
    assert [line.text for line in files["new.txt"].hunks[0].lines] == ["brand", "new"]
    assert (files["lib.py"].additions, files["lib.py"].deletions) == (2, 2)


def test_uncommitted_shows_only_what_changed_since_the_last_commit(clone):
    diff = diffs.diff(clone, "uncommitted")
    assert diff.base == "HEAD"
    assert sorted(by_path(diff)) == ["lib.py", "new.txt", "old.txt"]
    assert by_path(diff)["lib.py"].additions == 1


def test_lines_know_their_numbers_in_both_versions(clone):
    lib = by_path(diffs.diff(clone, "uncommitted"))["lib.py"]
    lines = [line for hunk in lib.hunks for line in hunk.lines]
    deleted = next(line for line in lines if line.kind == "delete")
    added = next(line for line in lines if line.kind == "add")
    assert (deleted.old, deleted.new, deleted.text) == (15, None, "line 15")
    assert (added.old, added.new, added.text) == (None, 15, "line fifteen")
    context = next(line for line in lines if line.kind == "context")
    assert context.old == context.new


def test_a_full_file_shows_every_line_with_the_changes(clone):
    lib = diffs.full_file(clone, "branch", "lib.py")
    assert lib is not None and len(lib.hunks) == 1
    lines = lib.hunks[0].lines
    assert [line.new for line in lines if line.kind != "delete"] == list(range(1, 21))
    assert (lib.additions, lib.deletions) == (2, 2)


def test_a_full_file_follows_renames_and_untracked_files(clone):
    renamed = diffs.full_file(clone, "branch", "README.txt", "README.md")
    assert renamed is not None and (renamed.status, renamed.old_path) == ("renamed", "README.md")
    new = diffs.full_file(clone, "branch", "new.txt")
    assert new is not None and [line.text for line in new.hunks[0].lines] == ["brand", "new"]
    assert diffs.full_file(clone, "uncommitted", "README.txt") is None


def test_without_a_default_branch_the_changes_are_those_since_head(tmp_path):
    git("init", "-b", "work", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("one\n")
    git("add", ".", cwd=tmp_path)
    git("commit", "-m", "one", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("two\n")
    diff = diffs.diff(tmp_path, "branch")
    assert diff.base == "HEAD"
    assert [file.path for file in diff.files] == ["a.txt"]


def test_a_repository_without_commits_shows_its_files_as_new(tmp_path):
    git("init", "-b", "main", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("one\n")
    diff = diffs.diff(tmp_path, "branch")
    assert [(file.path, file.status) for file in diff.files] == [("a.txt", "added")]


def test_a_long_diff_is_listed_without_its_lines():
    text = (
        "diff --git a/big b/big\nnew file mode 100644\n--- /dev/null\n+++ b/big\n"
        f"@@ -0,0 +1,{diffs.MAX_FILE_LINES + 1} @@\n"
        + "".join(f"+{number}\n" for number in range(diffs.MAX_FILE_LINES + 1))
    )
    [big] = diffs.parse(text)
    assert big.truncated and big.hunks == []
    assert big.additions == diffs.MAX_FILE_LINES + 1


def test_a_review_reads_as_a_message_to_an_agent():
    text = diffs.format_review(
        "origin/main",
        [
            ReviewComment(path="lib.py", start=5, end=6, quote="a\nb", body="Why?"),
            ReviewComment(path="lib.py", side="old", start=3, end=3, body="Keep this."),
            ReviewComment(path="docs.md", body="Rewrite."),
        ],
        "Mostly fine.",
    )
    assert text == (
        "Review comments from the user on the changes against origin/main:\n\n"
        "Mostly fine.\n\n"
        "## lib.py:5-6\n> a\n> b\nWhy?\n\n"
        "## lib.py:3 (old version)\nKeep this.\n\n"
        "## docs.md (whole file)\nRewrite.\n"
    )
    assert "without comments" in diffs.format_review("HEAD", [], "")


# -- reviews -------------------------------------------------------------------

RECORDER = 'cat -v > "$BYTEARMY_PANE.got"'  # writes down what is typed into it


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
    return path


def test_a_diff_pane_shows_the_changes_of_its_task(store, workspaces, task):
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    assert pane.kind == "diff"
    diff = workspaces.diff("demo", "work", pane.id, "branch")
    assert [file.path for file in diff.files] == ["a.txt"]
    full = workspaces.full_file("demo", "work", pane.id, "branch", "a.txt", "")
    assert [line.text for line in full.hunks[0].lines] == ["one"]
    with pytest.raises(InvalidValues):
        workspaces.full_file("demo", "work", pane.id, "branch", "missing.txt", "")


def test_a_draft_is_kept_until_the_pane_closes(store, workspaces, task):
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    assert workspaces.draft("demo", "work", pane.id) == Review()
    draft = Review(comments=[ReviewComment(path="a.txt", start=1, end=1, body="Hm.")])
    workspaces.save_draft("demo", "work", pane.id, draft)
    assert workspaces.draft("demo", "work", pane.id) == draft
    workspaces.close_pane("demo", "work", pane.id)
    assert not (task / ".bytearmy" / "reviews" / f"{pane.id}.json").exists()


def test_a_finished_review_is_typed_into_the_master(store, workspaces, task):
    tool(store, "bot", name="Bot", agent=True, command=RECORDER)
    master = workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
    workspaces.start("demo", "work", master.id)
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    review = Review(comments=[ReviewComment(path="a.txt", start=1, end=1, body="Hm.")])
    workspaces.submit_review("demo", "work", pane.id, review)

    got = task / f"{master.id}.got"
    wait_until(lambda: got.exists() and "Hm." in got.read_text(), "the master gets the review")
    assert got.read_text().startswith("^[[200~Review comments from the user")
    workspace = store.task("demo", "work").workspace
    assert pane.id not in workspace.panes
    assert store.log("demo", "work")[-1].text == "Finished a review with 1 comment"


def test_a_review_without_a_running_master_stays_open(store, workspaces, task):
    pane = workspaces.open_pane("demo", "work", PaneOpen(tool="diff"))
    with pytest.raises(NoMaster):
        workspaces.submit_review("demo", "work", pane.id, Review())
    assert pane.id in store.task("demo", "work").workspace.panes


def test_an_agent_asks_for_a_review_and_gets_it(core, monkeypatch, capsys):  # noqa: F811
    path = Path(core.store.task("demo", "work").path)
    git("init", "-b", "main", cwd=path)
    (path / "a.txt").write_text("one\n")
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
    assert pane["values"] == {
        "requester": me["BYTEARMY_PANE"],
        "mode": "branch",
        "note": "look at a.txt",
    }
    review = {"comments": [{"path": "a.txt", "start": 1, "end": 1, "body": "Fine."}]}
    assert core.http.post(f"{PANES}/{pane['id']}/review", json=review).status_code == 200
    waiting.join(timeout=5)

    assert codes == [0]
    out = capsys.readouterr().out
    assert "## a.txt:1\nFine." in out
    assert out.startswith(f"opened the review in pane {pane['id']}")


def test_an_agent_hears_when_the_review_is_closed(core):  # noqa: F811
    me = calling_pane(core)
    opened = core.http.post(PANES, json={"tool": "diff"})
    assert opened.status_code == 201
    core.http.delete(f"{PANES}/p2")
    state = core.workspaces.review_state(("demo", "work", me["BYTEARMY_PANE"]), "p2")
    assert state.done and state.text == "The user closed the review without finishing it."
