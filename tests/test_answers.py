"""Checks for draft answers: an agent drafts an answer to a GitHub conversation, the user
edits and approves or discards it in an answer pane, and the agent posts it with gh."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import wait_until
from test_agents import KEEP_ENV, key_of, tool
from test_api_panes import PANES, serving, started_task

from bytearmy.answers import AnswerCommands, AnswerFailed, parse_url, render
from bytearmy.models import Draft, DraftComment, PaneOpen
from bytearmy.store import Store
from bytearmy.themes import load_theme
from bytearmy.workspaces import Workspaces

RECORDER = 'cat -v > "$BYTEARMY_PANE.got"'  # writes down what is typed into it
PR = "https://github.com/owner/widgets/pull/12"

# Writes its arguments to gh-calls.txt beside itself and answers as `gh api --jq` would.
FAKE_GH = """\
import json, os, sys
from pathlib import Path
with Path(__file__).with_name("gh-calls.txt").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_GH_FAILS"):
    sys.exit("HTTP 404: Not Found")
path = sys.argv[2]
def comment(id, user, body, reply=None, **more):
    return {"id": id, "in_reply_to_id": reply, "user": user, "body": body,
            "created_at": "2026-11-02T09:00:00Z",
            "html_url": f"https://github.com/owner/widgets/pull/12#c{id}", **more}
if "POST" in sys.argv:
    print("https://github.com/owner/widgets/pull/12#issuecomment-999")
elif path == "repos/owner/widgets/issues/12":
    print(json.dumps({"title": "Fix login", "user": "alice", "body": "Login breaks.",
                      "created_at": "2026-11-01T10:00:00Z",
                      "html_url": "https://github.com/owner/widgets/pull/12"}))
elif path == "repos/owner/widgets/issues/12/comments":
    print(json.dumps(comment(345, "bob", "Why <b>two</b> calls?")))
elif path == "repos/owner/widgets/pulls/comments/78":
    print(json.dumps(comment(78, "alice", "Agreed?", 77)))
elif path == "repos/owner/widgets/pulls/12/comments":
    hunk = {"path": "login.py", "diff_hunk": "@@ -1 +1 @@\\n-a\\n+b"}
    print(json.dumps(comment(77, "bob", "Rename this.", **hunk)))
    print(json.dumps(comment(78, "alice", "Agreed?", 77, **hunk)))
    print(json.dumps(comment(90, "carol", "Another thread.", **hunk)))
else:
    sys.exit(f"unknown path {path}")
"""


@pytest.fixture
def gh(tmp_path) -> Path:
    script = tmp_path / "gh" / "fake_gh.py"
    script.parent.mkdir()
    script.write_text(FAKE_GH)
    return script


def gh_calls(gh: Path) -> list[list[str]]:
    log = gh.with_name("gh-calls.txt")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def core(tmp_path, gh):
    store = Store(tmp_path / "root")
    started_task(store)
    commands = AnswerCommands(gh=[sys.executable, str(gh)])
    workspaces = Workspaces(store, shell=["/bin/sh"], answer_commands=commands)
    with serving(store, workspaces) as live:
        yield live
    workspaces.shutdown()


def master(core) -> dict[str, str]:
    """Start the task's master, which writes down what is typed into it."""
    tool(core.store, "bot", name="Bot", agent=True, command=f"{KEEP_ENV}; {RECORDER}")
    pane = core.workspaces.open_pane("demo", "work", PaneOpen(tool="bot"))
    core.workspaces.start("demo", "work", pane.id)
    return key_of(core, pane.id)


def got(core, pane: str, text: str) -> str:
    file = Path(core.store.task("demo", "work").path) / f"{pane}.got"
    wait_until(lambda: file.exists() and text in file.read_text(), f"{pane} gets {text!r}")
    return file.read_text()


def answer_page(core, pane: str) -> str:
    return core.http.get(f"{PANES}/{pane}/page").json()["url"]


# -- what a URL points at ----------------------------------------------------------


def test_a_url_names_the_conversation_and_the_comment():
    assert parse_url(f"{PR}#issuecomment-345") == parse_url(f"{PR}#issuecomment-345")
    target = parse_url(f"{PR}#issuecomment-345")
    assert (target.owner, target.repo, target.number) == ("owner", "widgets", 12)
    assert (target.thread, target.comment) == ("issue", 345)
    assert (
        parse_url(f"{PR}#discussion_r78").thread,
        parse_url(f"{PR}#discussion_r78").comment,
    ) == (
        "review",
        78,
    )
    assert parse_url(f"{PR}/files#r78").comment == 78
    assert parse_url("https://github.com/owner/widgets/issues/3").thread == "issue"
    assert parse_url("https://github.com/owner/widgets/issues/3").comment is None
    assert parse_url(f"{PR}#pullrequestreview-5").comment is None
    with pytest.raises(AnswerFailed, match="is not the URL"):
        parse_url("https://example.com/somewhere")


def test_the_page_shows_the_conversation_escaped_and_the_draft_to_edit(tmp_path):
    draft = Draft(
        id="p2",
        url=f"{PR}#issuecomment-345",
        title="Fix <login>",
        owner="owner",
        repo="widgets",
        number=12,
        comments=[
            DraftComment(id="1", author="alice", body="Login breaks."),
            DraftComment(id="345", author="bob", body="Why <b>two</b>?"),
        ],
        focus="345",
        draft="Because </textarea><script>",
        agent="demo-work-master",
    )
    page = render(draft, load_theme(tmp_path))
    assert "Fix &lt;login&gt; #12" in page
    assert "Why &lt;b&gt;two&lt;/b&gt;?" in page
    assert 'class="comment focus"' in page and page.count("comment focus") == 1
    assert "Because &lt;/textarea&gt;&lt;script&gt;</textarea>" in page
    assert ">Discard</button>" in page and ">Approve</button>" in page


# -- drafting, deciding and sending ------------------------------------------------


def test_an_approved_answer_is_told_to_the_agent_and_then_sent(core, gh, bytearmy):
    me = master(core)

    code, said = bytearmy(me, "answer", "draft", f"{PR}#issuecomment-345", "--body", "Once.")

    assert code == 0 and said.startswith("opened the draft answer p2 for the user")
    pane = core.workspace()["panes"]["p2"]
    assert (pane["kind"], pane["highlighted"]) == ("answer", True)
    assert pane["title"] == "Draft answer: Fix login"
    url = answer_page(core, "p2")
    page = core.http.get(url).text
    assert "Login breaks." in page and "Why &lt;b&gt;two&lt;/b&gt; calls?" in page
    assert ">Once.</textarea>" in page

    # Nothing is sent before the user approves.
    code, said = bytearmy(me, "answer", "send", "p2")
    assert code == 1 and "is open, not approved" in said

    edited = "Only once now; see `login.py`."
    assert core.http.post(url + "/approve", json={"answer": edited}).status_code == 200

    told = got(core, me["BYTEARMY_PANE"], "answer send p2")
    assert "Draft answer approved" in told and "after editing it" in told
    assert edited in told and "bytearmy review --uncommitted" in told
    assert "p2" not in core.workspace()["panes"]
    assert core.http.post(url + "/approve", json={"answer": edited}).status_code == 409

    code, said = bytearmy(me, "answer", "send", "p2")

    assert (code, said) == (
        0,
        (
            "sent the answer: https://github.com/owner/widgets/pull/12#issuecomment-999\n"
            "the task now waits for a reply on GitHub (state waiting)"
        ),
    )
    assert gh_calls(gh)[-1] == [
        "api",
        "repos/owner/widgets/issues/12/comments",
        "--method",
        "POST",
        "-f",
        f"body={edited}",
        "--jq",
        ".html_url",
    ]
    task = core.store.task("demo", "work")
    assert (task.state, task.status) == ("waiting", "Waiting for a reply on GitHub")
    code, said = bytearmy(me, "answer", "send", "p2")
    assert code == 1 and "was already sent" in said
    assert len([call for call in gh_calls(gh) if "POST" in call]) == 1


def test_a_discarded_answer_tells_the_agent_the_user_answers_themselves(core, gh, bytearmy):
    me = master(core)
    bytearmy(me, "answer", "draft", PR, "--body", "Draft.")
    url = answer_page(core, "p2")

    assert core.http.post(url + "/discard", json={}).status_code == 200

    told = got(core, me["BYTEARMY_PANE"], "themselves")
    assert "Draft answer discarded" in told
    assert "p2" not in core.workspace()["panes"]
    code, said = bytearmy(me, "answer", "send", "p2")
    assert code == 1 and "is discarded, not approved" in said
    assert not [call for call in gh_calls(gh) if "POST" in call]


def test_closing_the_answer_pane_discards_the_draft(core, bytearmy):
    me = master(core)
    bytearmy(me, "answer", "draft", PR, "--body", "Draft.")

    core.http.delete(f"{PANES}/p2")

    assert "Draft answer discarded" in got(core, me["BYTEARMY_PANE"], "themselves")


def test_an_answer_in_a_review_thread_goes_to_that_thread(core, gh, bytearmy):
    me = master(core)
    bytearmy(me, "answer", "draft", f"{PR}#discussion_r78", "--body", "Renamed.")
    url = answer_page(core, "p2")
    page = core.http.get(url).text
    assert "Rename this." in page and "Agreed?" in page and "Another thread." not in page
    assert "login.py" in page and "+b" in page

    core.http.post(url + "/approve", json={"answer": "Renamed."})
    told = got(core, me["BYTEARMY_PANE"], "answer send p2")
    assert "after editing it" not in told
    assert bytearmy(me, "answer", "send", "p2")[0] == 0

    assert gh_calls(gh)[-1][1] == "repos/owner/widgets/pulls/12/comments/77/replies"


def test_an_empty_answer_cannot_be_approved(core, bytearmy):
    bytearmy(master(core), "answer", "draft", PR, "--body", "Draft.")
    url = answer_page(core, "p2")
    answer = core.http.post(url + "/approve", json={"answer": "  "})
    assert answer.status_code == 400
    assert "p2" in core.workspace()["panes"]


def test_the_answer_page_needs_the_key(core, bytearmy):
    me = master(core)
    bytearmy(me, "answer", "draft", PR, "--body", "Draft.")
    url = answer_page(core, "p2")
    forged = url.replace("/answers/", "/answers/x")
    assert core.http.get(forged).status_code == 404
    assert core.http.post(forged + "/discard", json={}).status_code == 404
    assert "p2" in core.workspace()["panes"]


def test_what_gh_says_when_it_fails_reaches_the_agent(core, bytearmy, monkeypatch):
    me = master(core)
    monkeypatch.setenv("FAKE_GH_FAILS", "1")
    code, said = bytearmy(me, "answer", "draft", PR, "--body", "Draft.")
    assert code == 1 and said == "bytearmy: `gh api` failed: HTTP 404: Not Found"
    assert list(core.workspace()["panes"]) == ["p1"]
