"""Checks for the inbox and its GitHub source."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from bytearmy.api import create_app
from bytearmy.inbox import GitHubSource, Inbox, SourceFailed, browser_url
from bytearmy.store import Store

TOKEN = "secret"


def thread(number: int, updated: str = "2026-11-15T10:00:00Z", **subject) -> dict:
    return {
        "id": str(number),
        "unread": True,
        "reason": "mention",
        "updated_at": updated,
        "subject": {
            "title": f"Thread {number}",
            "type": "PullRequest",
            "url": f"https://api.github.com/repos/acme/widgets/pulls/{number}",
            **subject,
        },
        "repository": {"full_name": "acme/widgets", "html_url": "https://github.com/acme/widgets"},
    }


class FakeGh:
    """Stands in for `gh`: answers /notifications with `threads` and records every call."""

    def __init__(self) -> None:
        self.threads: list[dict] = []
        self.calls: list[list[str]] = []
        self.failure: str | None = None

    def __call__(self, command) -> str:
        self.calls.append(list(command))
        if self.failure:
            raise SourceFailed(self.failure)
        return json.dumps(self.threads)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def gh():
    return FakeGh()


@pytest.fixture
def inbox(store, gh):
    created = Inbox(store, [GitHubSource(run=gh)], now=lambda: datetime(2026, 11, 15, tzinfo=UTC))
    yield created
    created.shutdown()


def settle(inbox: Inbox) -> None:
    """Wait for the changes passed on to the sources."""
    for worker in inbox._workers:
        worker.join(timeout=5)


@pytest.mark.parametrize(
    ("api_url", "page"),
    [
        ("https://api.github.com/repos/o/r/pulls/7", "https://github.com/o/r/pull/7"),
        ("https://api.github.com/repos/o/r/issues/3", "https://github.com/o/r/issues/3"),
        ("https://api.github.com/repos/o/r/commits/abc", "https://github.com/o/r/commit/abc"),
        ("https://api.github.com/repos/o/r/releases/9", "https://github.com/o/r/releases"),
        (None, "https://github.com/o/r"),
    ],
)
def test_an_api_url_becomes_the_page_a_browser_shows(api_url, page):
    assert browser_url(api_url, "https://github.com/o/r") == page


def test_new_notifications_arrive_newest_first_and_move_the_version_on(store, gh, inbox):
    gh.threads = [thread(1, "2026-11-15T09:00:00Z"), thread(2, "2026-11-15T10:00:00Z")]
    version = store.version

    inbox.check()

    messages = inbox.state().messages
    assert [message.id for message in messages] == ["github:2", "github:1"]
    assert messages[0].url == "https://github.com/acme/widgets/pull/2"
    assert messages[0].context == "acme/widgets"
    assert store.version > version


def test_a_check_without_news_leaves_the_version(store, gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()
    version = store.version

    inbox.check()

    assert store.version == version
    assert len(inbox.state().messages) == 1


def test_marking_read_tells_github(gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()

    inbox.mark("github:1", unread=False)
    settle(inbox)

    assert not inbox.state().messages[0].unread
    assert ["gh", "api", "-X", "PATCH", "/notifications/threads/1"] in gh.calls


def test_marking_unread_stays_in_bytearmy(gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()
    inbox.mark("github:1", unread=False)
    settle(inbox)
    calls = len(gh.calls)

    inbox.mark("github:1", unread=True)
    settle(inbox)

    assert inbox.state().messages[0].unread
    assert len(gh.calls) == calls


def test_a_read_message_stays_read_until_new_activity(gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()
    inbox.mark("github:1", unread=False)
    settle(inbox)

    inbox.check()  # GitHub may not have heard of it yet
    assert not inbox.state().messages[0].unread

    gh.threads = [thread(1, "2026-11-15T11:00:00Z")]
    inbox.check()
    assert inbox.state().messages[0].unread


def test_a_dismissed_message_stays_hidden_until_new_activity(gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()

    inbox.dismiss("github:1")
    settle(inbox)
    inbox.check()

    assert inbox.state().messages[0].dismissed
    assert ["gh", "api", "-X", "DELETE", "/notifications/threads/1"] in gh.calls

    gh.threads = [thread(1, "2026-11-15T11:00:00Z")]
    inbox.check()
    assert not inbox.state().messages[0].dismissed


def test_a_message_read_on_github_turns_read(gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()

    gh.threads = []
    inbox.check()

    assert not inbox.state().messages[0].unread


def test_a_failing_gh_is_reported_and_cleared_by_the_next_good_check(gh, inbox):
    gh.failure = "gh: not logged in"
    inbox.check()
    assert inbox.state().errors == {"github": "gh: not logged in"}

    gh.failure = None
    gh.threads = [thread(1)]
    inbox.check()
    assert inbox.state().errors == {}


def test_the_thread_asks_at_once_and_again_when_woken(gh, inbox):
    inbox._poll_seconds = 60
    inbox.start()
    deadline = time.monotonic() + 5
    while len(gh.calls) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    gh.threads = [thread(1)]
    inbox.refresh()
    while not inbox.state().messages and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(inbox.state().messages) == 1
    inbox.shutdown()
    assert not inbox._thread.is_alive()


def test_the_api_shows_the_inbox_and_takes_changes(store, gh, inbox):
    gh.threads = [thread(1)]
    inbox.check()
    app = create_app(store, token=TOKEN, inbox=inbox)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        assert client.get("/api/state").json()["inbox"]["messages"][0]["id"] == "github:1"

        read = client.post("/api/inbox/github:1/read", json={"unread": False})
        assert read.json()["inbox"]["messages"][0]["unread"] is False

        dismissed = client.post("/api/inbox/github:1/dismiss")
        assert dismissed.json()["inbox"]["messages"][0]["dismissed"] is True

        assert client.post("/api/inbox/github:9/dismiss").status_code == 404
        assert client.post("/api/inbox/refresh").status_code == 200
        settle(inbox)


def test_the_view_is_part_of_the_ui_state(store):
    app = create_app(store, token=TOKEN)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        assert client.get("/api/state").json()["ui"]["view"] == "workspace"
        assert client.patch("/api/ui", json={"view": "inbox"}).json()["ui"]["view"] == "inbox"
