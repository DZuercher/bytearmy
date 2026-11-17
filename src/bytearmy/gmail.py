"""Gmail as a source of the inbox: the unread mail in the inbox of each account the config names.

An account is asked either through the Gmail API, with an OAuth client of the user's own
Google Cloud project, or over IMAP with an app password. Each email thread is one message
of the inbox. bytearmy only reads: marking a message read or dismissing it stays in
bytearmy. The secrets of an account are kept in `<root>/.bytearmy/secrets/gmail/<address>.json`;
`bytearmy --gmail-login <address>` writes them.
"""

from __future__ import annotations

import base64
import email
import email.policy
import getpass
import hashlib
import http.server
import imaplib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from bytearmy.config import Config, GmailAccount
from bytearmy.inbox import ERROR_CHARACTERS, SourceFailed
from bytearmy.models import Message
from bytearmy.store import METADATA_DIR

SECRETS_DIR = Path(METADATA_DIR) / "secrets" / "gmail"
QUERY = "is:unread in:inbox"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
IMAP_HOST = "imap.gmail.com"
PAGE = 50  # threads asked for in one turn
IMAP_MESSAGES = 200  # the newest unread mails fetched over IMAP in one turn


def secrets_file(root: Path, address: str) -> Path:
    return root / SECRETS_DIR / f"{address}.json"


def read_secrets(root: Path, address: str) -> dict[str, str]:
    file = secrets_file(root, address)
    if not file.is_file():
        raise SourceFailed(f"not logged in; run `bytearmy --gmail-login {address}`")
    try:
        return json.loads(file.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SourceFailed(f"{file} cannot be read: {error}") from error


def write_secrets(root: Path, address: str, values: dict[str, str]) -> Path:
    """Keep an account's secrets in a file only the user can read."""
    file = secrets_file(root, address)
    file.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(file.parent, 0o700)
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        json.dump(values, out, indent=2)
    os.chmod(file, 0o600)
    return file


def thread_url(address: str, thread_id: str) -> str:
    """The thread's page in Gmail, in the account it belongs to."""
    return f"https://mail.google.com/mail/?authuser={urllib.parse.quote(address)}#all/{thread_id}"


class Mail:
    """One unread mail, as either backend reports it."""

    def __init__(self, thread_id: str, subject: str, sender: str, received: datetime) -> None:
        self.thread_id = thread_id
        self.subject = subject
        self.sender = sender
        self.received = received


class Backend(Protocol):
    def unread(self) -> tuple[list[Mail], bool]:
        """The unread mails of the inbox, and whether these are all of them."""
        ...


def threads(address: str, mails: Sequence[Mail]) -> list[Message]:
    """One message per thread: subject, sender and time of its newest unread mail."""
    newest: dict[str, Mail] = {}
    for mail in mails:
        known = newest.get(mail.thread_id)
        if known is None or mail.received > known.received:
            newest[mail.thread_id] = mail
    return [
        Message(
            id=f"gmail:{address}:{mail.thread_id}",
            source="gmail",
            title=mail.subject or "(no subject)",
            kind="Email",
            context=mail.sender,
            reason=address,
            url=thread_url(address, mail.thread_id),
            updated_at=mail.received.astimezone(UTC).isoformat(timespec="seconds"),
        )
        for mail in newest.values()
    ]


class GmailSource:
    """The unread mail of one Gmail account."""

    def __init__(
        self,
        address: str,
        backend: Backend,
        settings: Callable[[], Config] = Config,
    ) -> None:
        self.address = address
        self.name = f"gmail:{address}"
        self._backend = backend
        self._settings = settings

    def enabled(self) -> bool:
        gmail = self._settings().inbox.gmail
        return gmail.enabled and any(account.address == self.address for account in gmail.accounts)

    def fetch(self) -> tuple[list[Message], bool]:
        mails, complete = self._backend.unread()
        return threads(self.address, mails), complete

    # bytearmy only reads Gmail: what the user does with a message stays in bytearmy.
    def mark_read(self, message: Message) -> None:
        pass

    def dismiss(self, message: Message) -> None:
        pass


def sources(root: Path, settings: Callable[[], Config]) -> list[GmailSource]:
    """A source for each Gmail account of the config."""
    made: list[GmailSource] = []
    for account in settings().inbox.gmail.accounts:
        backend: Backend = (
            OAuthBackend(root, account.address)
            if account.method == "oauth"
            else ImapBackend(root, account.address)
        )
        made.append(GmailSource(account.address, backend, settings))
    return made


# -- the Gmail API ----------------------------------------------------------------

Fetcher = Callable[[urllib.request.Request], dict]


def fetch_json(request: urllib.request.Request) -> dict:
    """The JSON a request answers with; a failing request raises SourceFailed."""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise SourceFailed(f"{error.code} {error.reason}: {body}"[:ERROR_CHARACTERS]) from error
    except (OSError, json.JSONDecodeError) as error:
        raise SourceFailed(str(error)[:ERROR_CHARACTERS]) from error


def post_form(url: str, fields: dict[str, str]) -> urllib.request.Request:
    return urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")


class OAuthBackend:
    """Asks the Gmail API, with the refresh token `--gmail-login` kept."""

    def __init__(
        self,
        root: Path,
        address: str,
        fetch: Fetcher = fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = root
        self._address = address
        self._fetch = fetch
        self._clock = clock
        self._token = ""
        self._expires = 0.0

    def _access_token(self) -> str:
        if self._token and self._clock() < self._expires:
            return self._token
        kept = read_secrets(self._root, self._address)
        if not kept.get("refresh_token"):
            raise SourceFailed(f"no OAuth login; run `bytearmy --gmail-login {self._address}`")
        answer = self._fetch(
            post_form(
                TOKEN_URL,
                {
                    "client_id": kept.get("client_id", ""),
                    "client_secret": kept.get("client_secret", ""),
                    "refresh_token": kept["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
        )
        self._token = answer.get("access_token", "")
        if not self._token:
            raise SourceFailed("Google gave no access token")
        # A minute early, so a token never runs out during a turn.
        self._expires = self._clock() + float(answer.get("expires_in", 3600)) - 60
        return self._token

    def _get(self, path: str, query: list[tuple[str, str]]) -> dict:
        url = f"{API_URL}/{path}?{urllib.parse.urlencode(query)}"
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        return self._fetch(urllib.request.Request(url, headers=headers))

    def unread(self) -> tuple[list[Mail], bool]:
        listed = self._get("threads", [("q", QUERY), ("maxResults", str(PAGE))])
        mails: list[Mail] = []
        for found in listed.get("threads", []):
            thread = self._get(
                f"threads/{found['id']}",
                [
                    ("format", "metadata"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "From"),
                ],
            )
            for message in thread.get("messages", []):
                if "UNREAD" not in message.get("labelIds", []):
                    continue
                headers = {
                    header["name"].lower(): header["value"]
                    for header in message.get("payload", {}).get("headers", [])
                }
                mails.append(
                    Mail(
                        thread_id=found["id"],
                        subject=headers.get("subject", ""),
                        sender=headers.get("from", ""),
                        received=datetime.fromtimestamp(
                            int(message.get("internalDate", "0")) / 1000, UTC
                        ),
                    )
                )
        return mails, "nextPageToken" not in listed


# -- IMAP -------------------------------------------------------------------------------

FETCHED = re.compile(rb'X-GM-THRID (\d+).*?INTERNALDATE "([^"]+)"', re.DOTALL)


class ImapBackend:
    """Asks Gmail's IMAP server, with the app password `--gmail-login` kept. The inbox is
    opened read-only and mails are fetched with PEEK, so nothing turns read."""

    def __init__(
        self,
        root: Path,
        address: str,
        connect: Callable[[], imaplib.IMAP4] = lambda: imaplib.IMAP4_SSL(IMAP_HOST, timeout=30),
    ) -> None:
        self._root = root
        self._address = address
        self._connect = connect

    def unread(self) -> tuple[list[Mail], bool]:
        password = read_secrets(self._root, self._address).get("app_password", "")
        if not password:
            raise SourceFailed(f"no app password; run `bytearmy --gmail-login {self._address}`")
        try:
            imap = self._connect()
            try:
                imap.login(self._address, password)
                imap.select("INBOX", readonly=True)
                _, found = imap.uid("SEARCH", "UNSEEN")
                uids = found[0].split() if found and found[0] else []
                newest = uids[-IMAP_MESSAGES:]
                mails = self._mails(imap, newest) if newest else []
            finally:
                try:
                    imap.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
        except (imaplib.IMAP4.error, OSError) as error:
            raise SourceFailed(str(error)[:ERROR_CHARACTERS]) from error
        return mails, len(newest) == len(uids)

    def _mails(self, imap: imaplib.IMAP4, uids: list[bytes]) -> list[Mail]:
        _, answer = imap.uid(
            "FETCH",
            b",".join(uids).decode(),
            "(X-GM-THRID INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])",
        )
        mails: list[Mail] = []
        for part in answer:
            if not isinstance(part, tuple):
                continue  # the closing parenthesis of a mail
            described, header = part
            match = FETCHED.search(described)
            if not match:
                continue
            thread, received = match.groups()
            headers = email.message_from_bytes(header, policy=email.policy.default)
            mails.append(
                Mail(
                    # The API's thread id is the same number, in hex.
                    thread_id=format(int(thread), "x"),
                    subject=str(headers.get("Subject", "")),
                    sender=str(headers.get("From", "")),
                    received=datetime.strptime(received.decode(), "%d-%b-%Y %H:%M:%S %z"),
                )
            )
        return mails


# -- logging in --------------------------------------------------------------------------


def login(root: Path, account: GmailAccount, ask: Callable[[str], str] = getpass.getpass) -> Path:
    """Ask the user for an account's secrets and keep them; the file they went to."""
    if account.method == "imap":
        password = ask(f"App password of {account.address} (spaces are ignored): ")
        return write_secrets(root, account.address, {"app_password": password.replace(" ", "")})
    client_id = input("Client ID of your Google Cloud OAuth client (Desktop app): ").strip()
    client_secret = ask("Its client secret: ").strip()
    refresh_token = oauth_consent(account.address, client_id, client_secret)
    return write_secrets(
        root,
        account.address,
        {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token},
    )


def oauth_consent(address: str, client_id: str, client_secret: str) -> str:
    """Let the user consent in the browser to bytearmy reading their mail; the refresh token.

    Google sends the browser back to a server on a free port of 127.0.0.1, as it does for
    desktop apps; PKCE keeps the code useless to anyone who intercepts it.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if query.get("state") != state:
                self.send_error(400, "unexpected request")
                return
            got.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"bytearmy got the answer; you can close this page.")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = f"{AUTH_URL}?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": address,
            "state": state,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
        }
    )
    print(f"Opening the browser to log in. If it does not open, go to:\n{url}")
    webbrowser.open(url)
    waiting = threading.Thread(target=lambda: _serve_until(server, got), daemon=True)
    waiting.start()
    waiting.join(timeout=300)
    server.server_close()
    if "code" not in got:
        raise SourceFailed(got.get("error") or "no answer from Google within 5 minutes")
    answer = fetch_json(
        post_form(
            TOKEN_URL,
            {
                "code": got["code"],
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    )
    if not answer.get("refresh_token"):
        raise SourceFailed("Google gave no refresh token")
    return answer["refresh_token"]


def _serve_until(server: http.server.HTTPServer, got: dict[str, str]) -> None:
    while not got:
        server.handle_request()
