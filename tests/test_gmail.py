"""Checks for the Gmail source of the inbox, with both its backends."""

from __future__ import annotations

import json
import stat
import urllib.parse
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bytearmy.config import Config, GmailAccount
from bytearmy.gmail import (
    GmailSource,
    ImapBackend,
    Mail,
    OAuthBackend,
    read_secrets,
    secrets_file,
    threads,
    write_secrets,
)
from bytearmy.inbox import Inbox, SourceFailed
from bytearmy.store import Store

ADDRESS = "me@gmail.com"


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 15, hour, minute, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


def test_a_thread_is_one_message_with_its_newest_unread_mail():
    mails = [
        Mail("18c1", "Lunch?", "Ann <ann@x.org>", at(9)),
        Mail("18c1", "Re: Lunch?", "Bob <bob@x.org>", at(11)),
        Mail("18c1", "Re: Lunch?", "Cid <cid@x.org>", at(10)),
        Mail("18c2", "", "Dee <dee@x.org>", at(8)),
    ]

    messages = {message.id: message for message in threads(ADDRESS, mails)}

    assert set(messages) == {f"gmail:{ADDRESS}:18c1", f"gmail:{ADDRESS}:18c2"}
    lunch = messages[f"gmail:{ADDRESS}:18c1"]
    assert (lunch.title, lunch.context, lunch.updated_at) == (
        "Re: Lunch?",
        "Bob <bob@x.org>",
        "2026-11-15T11:00:00+00:00",
    )
    assert (lunch.source, lunch.reason) == ("gmail", ADDRESS)
    assert lunch.url == "https://mail.google.com/mail/?authuser=me%40gmail.com#all/18c1"
    assert messages[f"gmail:{ADDRESS}:18c2"].title == "(no subject)"


# -- the secrets ------------------------------------------------------------------------


def test_the_secrets_are_kept_where_only_the_user_can_read_them(store):
    file = write_secrets(store.root, ADDRESS, {"app_password": "abcd"})

    assert file == secrets_file(store.root, ADDRESS)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE(file.parent.stat().st_mode) == 0o700
    assert read_secrets(store.root, ADDRESS) == {"app_password": "abcd"}


def test_without_secrets_the_source_says_how_to_log_in(store):
    with pytest.raises(SourceFailed, match=f"bytearmy --gmail-login {ADDRESS}"):
        read_secrets(store.root, ADDRESS)


def test_an_address_cannot_lead_the_secrets_out_of_their_directory():
    with pytest.raises(ValidationError):
        GmailAccount(address="../../x@gmail.com", method="imap")


# -- the Gmail API -----------------------------------------------------------------------


class FakeGoogle:
    """Stands in for Google's token endpoint and the Gmail API."""

    def __init__(self) -> None:
        self.threads: dict[str, list[dict]] = {}
        self.more = False
        self.requests: list = []

    def __call__(self, request) -> dict:
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        if url.netloc == "oauth2.googleapis.com":
            form = dict(urllib.parse.parse_qsl(request.data.decode()))
            assert form["grant_type"] == "refresh_token"
            assert form["refresh_token"] == "refresh"
            return {"access_token": "access", "expires_in": 3600}
        assert request.get_header("Authorization") == "Bearer access"
        if url.path.endswith("/threads"):
            assert dict(urllib.parse.parse_qsl(url.query))["q"] == "is:unread in:inbox"
            listed: dict = {"threads": [{"id": thread} for thread in self.threads]}
            if self.more:
                listed["nextPageToken"] = "next"
            return listed
        return {"messages": self.threads[url.path.rsplit("/", 1)[1]]}

    def api_calls(self) -> int:
        return sum("gmail.googleapis.com" in request.full_url for request in self.requests)

    def token_calls(self) -> int:
        return len(self.requests) - self.api_calls()


def mail(subject: str, sender: str, when: datetime, unread: bool = True) -> dict:
    return {
        "labelIds": ["INBOX", *(["UNREAD"] if unread else [])],
        "internalDate": str(int(when.timestamp() * 1000)),
        "payload": {
            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}]
        },
    }


@pytest.fixture
def google(store):
    write_secrets(
        store.root, ADDRESS, {"client_id": "id", "client_secret": "s", "refresh_token": "refresh"}
    )
    return FakeGoogle()


def test_the_api_reports_only_the_unread_mails_of_a_thread(store, google):
    google.threads = {
        "18c1": [mail("Lunch?", "ann@x.org", at(9)), mail("Re: Lunch?", "bob@x.org", at(12), False)]
    }
    backend = OAuthBackend(store.root, ADDRESS, fetch=google)

    mails, complete = backend.unread()

    assert [(found.thread_id, found.sender, found.received) for found in mails] == [
        ("18c1", "ann@x.org", at(9))
    ]
    assert complete


def test_the_api_is_incomplete_when_more_threads_wait(store, google):
    google.more = True
    assert OAuthBackend(store.root, ADDRESS, fetch=google).unread() == ([], False)


def test_the_access_token_is_used_until_it_runs_out(store, google):
    now = [0.0]
    backend = OAuthBackend(store.root, ADDRESS, fetch=google, clock=lambda: now[0])

    backend.unread()
    backend.unread()
    assert google.token_calls() == 1

    now[0] = 3600.0
    backend.unread()
    assert google.token_calls() == 2


# -- IMAP ---------------------------------------------------------------------------------


class FakeImap:
    """Stands in for Gmail's IMAP server, as imaplib hands its answers on."""

    def __init__(self, mails: list[tuple[int, int, str, bytes]]) -> None:
        self.mails = mails  # uid, X-GM-THRID, INTERNALDATE, header
        self.calls: list[tuple] = []

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid, *_ in self.mails)]
        wanted = args[0].split(",")
        answer: list = []
        for uid, thread, date, header in self.mails:
            if str(uid) in wanted:
                described = (
                    f'{uid} (X-GM-THRID {thread} INTERNALDATE "{date}" UID {uid} '
                    f"BODY[HEADER.FIELDS (SUBJECT FROM)] {{{len(header)}}}"
                ).encode()
                answer += [(described, header), b")"]
        return "OK", answer

    def logout(self):
        self.calls.append(("logout",))


@pytest.fixture
def imap_secrets(store):
    write_secrets(store.root, ADDRESS, {"app_password": "abcdefgh"})


def test_imap_reads_the_unread_mails_without_marking_them_read(store, imap_secrets):
    server = FakeImap(
        [
            (
                7,
                0x18BDAA6E0DE0BC41,
                "15-Nov-2026 10:30:00 +0100",
                b"Subject: =?utf-8?q?Gr=C3=BCezi?=\r\nFrom: Ann <ann@x.org>\r\n\r\n",
            )
        ]
    )

    mails, complete = ImapBackend(store.root, ADDRESS, connect=lambda: server).unread()

    [found] = mails
    assert found.thread_id == "18bdaa6e0de0bc41"  # the number in hex, as the API has it
    assert (found.subject, found.sender) == ("Grüezi", "Ann <ann@x.org>")
    assert found.received == at(9, 30)
    assert complete
    assert ("login", ADDRESS, "abcdefgh") in server.calls
    assert ("select", "INBOX", True) in server.calls
    fetch = next(call for call in server.calls if call[:2] == ("uid", "FETCH"))
    assert "BODY.PEEK[" in fetch[3]
    assert server.calls[-1] == ("logout",)


def test_imap_fetches_only_the_newest_unread_mails(store, imap_secrets, monkeypatch):
    monkeypatch.setattr("bytearmy.gmail.IMAP_MESSAGES", 2)
    header = b"Subject: s\r\nFrom: f\r\n\r\n"
    date = "15-Nov-2026 10:30:00 +0000"
    server = FakeImap([(uid, uid, date, header) for uid in (3, 5, 9)])

    mails, complete = ImapBackend(store.root, ADDRESS, connect=lambda: server).unread()

    assert sorted(found.thread_id for found in mails) == ["5", "9"]
    assert not complete


def test_a_failing_imap_server_is_reported(store, imap_secrets):
    def refuse():
        raise OSError("connection refused")

    with pytest.raises(SourceFailed, match="connection refused"):
        ImapBackend(store.root, ADDRESS, connect=refuse).unread()


# -- in the inbox -------------------------------------------------------------------------


class FakeBackend:
    def __init__(self) -> None:
        self.mails: list[Mail] = []
        self.failure: str | None = None

    def unread(self):
        if self.failure:
            raise SourceFailed(self.failure)
        return self.mails, True


def two_accounts() -> Config:
    return Config.model_validate(
        {
            "inbox": {
                "gmail": {
                    "accounts": [
                        {"address": "a@gmail.com", "method": "imap"},
                        {"address": "b@work.com", "method": "oauth"},
                    ]
                }
            }
        }
    )


@pytest.fixture
def backends():
    return {"a@gmail.com": FakeBackend(), "b@work.com": FakeBackend()}


@pytest.fixture
def inbox(store, backends):
    created = Inbox(
        store,
        [GmailSource(address, backend, two_accounts) for address, backend in backends.items()],
        settings=two_accounts,
    )
    yield created
    created.shutdown()


def test_one_account_reading_its_mail_leaves_the_other_accounts_unread(inbox, backends):
    backends["a@gmail.com"].mails = [Mail("1", "s", "f", at(9))]
    backends["b@work.com"].mails = [Mail("2", "s", "f", at(9))]
    inbox.check()

    backends["a@gmail.com"].mails = []  # read in Gmail
    inbox.check()

    unread = {message.id: message.unread for message in inbox.state().messages}
    assert unread == {"gmail:a@gmail.com:1": False, "gmail:b@work.com:2": True}


def test_each_account_reports_its_own_failure(inbox, backends):
    backends["b@work.com"].failure = "not logged in"
    backends["a@gmail.com"].mails = [Mail("1", "s", "f", at(9))]

    inbox.check()

    assert inbox.state().errors == {"gmail:b@work.com": "not logged in"}
    assert len(inbox.state().messages) == 1


def test_an_account_taken_out_of_the_config_is_no_longer_asked(store, backends):
    calls = []
    backend = backends["a@gmail.com"]
    backend.unread = lambda: calls.append(1) or ([], True)
    nobody = Config()
    made = Inbox(
        store, [GmailSource("a@gmail.com", backend, lambda: nobody)], settings=lambda: nobody
    )

    made.check()

    assert calls == []


def test_reading_and_dismissing_mail_stays_in_bytearmy(inbox, backends):
    backends["a@gmail.com"].mails = [Mail("1", "s", "f", at(9))]
    inbox.check()

    inbox.mark("gmail:a@gmail.com:1", unread=False)
    inbox.dismiss("gmail:a@gmail.com:1")
    for worker in inbox._workers:
        worker.join(timeout=5)

    [message] = inbox.state().messages
    assert (message.unread, message.dismissed) == (False, True)
    assert inbox.state().errors == {}


def test_the_config_names_the_gmail_accounts():
    config = Config.model_validate(
        json.loads('{"inbox": {"gmail": {"accounts": [{"address": "x@y.ch", "method": "oauth"}]}}}')
    )
    assert config.inbox.gmail.accounts == [GmailAccount(address="x@y.ch", method="oauth")]
