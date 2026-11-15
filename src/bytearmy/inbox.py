"""The inbox: messages from sources outside bytearmy, gathered in the background.

A source is anything that can be asked for the user's unread messages: GitHub now; email,
Slack or Webex later. A thread asks every source in turns and keeps what they report in
`<root>/.bytearmy/inbox.json`. When something new arrives, the store's version moves on, so
the window shows it at once.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from bytearmy.models import InboxState, Message
from bytearmy.store import METADATA_DIR, Store, _write_json

POLL_SECONDS = 60.0  # how often the sources are asked; GitHub asks for no more than once a minute
KEPT_MESSAGES = 500  # the oldest messages beyond this are forgotten
ERROR_CHARACTERS = 300  # how much of a source's complaint is kept


class SourceFailed(Exception):
    """args: why the source could not be asked."""


class MessageNotFound(Exception):
    """args: the id of the message."""


class Source(Protocol):
    name: str

    def fetch(self) -> tuple[list[Message], bool]:
        """The unread messages, and whether these are all of them."""
        ...

    def mark_read(self, message: Message) -> None: ...

    def dismiss(self, message: Message) -> None: ...


Runner = Callable[[Sequence[str]], str]


def run_command(command: Sequence[str]) -> str:
    """The output of a command; a failing command raises SourceFailed with its complaint."""
    try:
        done = subprocess.run(
            list(command), capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SourceFailed(str(error)) from error
    if done.returncode != 0:
        raise SourceFailed((done.stderr or done.stdout).strip()[:ERROR_CHARACTERS])
    return done.stdout


def browser_url(api_url: str | None, repository_page: str) -> str:
    """The page of a GitHub API URL, such as an issue's; the repository's page if unknown."""
    match = re.fullmatch(
        r"https://api\.github\.com/repos/([^/]+/[^/]+)/(issues|pulls|commits|releases)/(.+)",
        api_url or "",
    )
    if not match:
        return repository_page
    repository, kind, rest = match.groups()
    kind = {"pulls": "pull", "commits": "commit"}.get(kind, kind)
    if kind == "releases":
        return f"https://github.com/{repository}/releases"
    return f"https://github.com/{repository}/{kind}/{rest}"


class GitHubSource:
    """The user's GitHub notifications, asked for with the `gh` they are logged in with."""

    name = "github"
    PAGE = 50

    def __init__(self, run: Runner = run_command, gh: Sequence[str] = ("gh",)) -> None:
        self._run = run
        self._gh = list(gh)

    def fetch(self) -> tuple[list[Message], bool]:
        output = self._run([*self._gh, "api", f"/notifications?per_page={self.PAGE}"])
        try:
            threads = json.loads(output)
        except json.JSONDecodeError as error:
            raise SourceFailed(f"GitHub answered with no list: {output[:100]!r}") from error
        messages = [self._message(thread) for thread in threads]
        return messages, len(threads) < self.PAGE

    def _message(self, thread: dict) -> Message:
        subject = thread.get("subject") or {}
        repository = thread.get("repository") or {}
        return Message(
            id=f"github:{thread['id']}",
            source="github",
            title=subject.get("title") or "(no title)",
            kind=subject.get("type") or "",
            context=repository.get("full_name") or "",
            reason=thread.get("reason") or "",
            url=browser_url(subject.get("url"), repository.get("html_url") or ""),
            updated_at=thread.get("updated_at") or "",
            unread=bool(thread.get("unread", True)),
        )

    def _thread(self, message: Message) -> str:
        return message.id.removeprefix("github:")

    def mark_read(self, message: Message) -> None:
        self._run(
            [*self._gh, "api", "-X", "PATCH", f"/notifications/threads/{self._thread(message)}"]
        )

    def dismiss(self, message: Message) -> None:
        # Marking a thread done takes it out of GitHub's inbox as well.
        self._run(
            [*self._gh, "api", "-X", "DELETE", f"/notifications/threads/{self._thread(message)}"]
        )


class Inbox:
    """Asks the sources for messages in a thread of its own and keeps them in the store's root."""

    def __init__(
        self,
        store: Store,
        sources: Sequence[Source] = (),
        poll_seconds: float = POLL_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._sources = {source.name: source for source in sources}
        self._poll_seconds = poll_seconds
        self._now = now
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []

    @property
    def _file(self):
        return self._store.root / METADATA_DIR / "inbox.json"

    def state(self) -> InboxState:
        with self._lock:
            if not self._file.exists():
                return InboxState()
            return InboxState.model_validate_json(self._file.read_text())

    def _save(self, state: InboxState) -> None:
        _write_json(self._file, state)
        self._store._changed()

    # -- the thread ----------------------------------------------------------

    def start(self) -> None:
        if not self._sources or self._thread:
            return
        self._thread = threading.Thread(target=self._poll, name="inbox", daemon=True)
        self._thread.start()

    def refresh(self) -> None:
        """Ask the sources now instead of at the next turn."""
        self._wake.set()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in [self._thread, *self._workers]:
            if thread:
                thread.join(timeout=5)

    def _poll(self) -> None:
        while not self._stop.is_set():
            self.check()
            self._wake.wait(self._poll_seconds)
            self._wake.clear()

    def check(self) -> None:
        """Ask every source once and keep what is new."""
        reports: dict[str, tuple[list[Message], bool] | str] = {}
        for name, source in self._sources.items():
            try:
                reports[name] = source.fetch()
            except SourceFailed as error:
                reports[name] = str(error) or "failed"
        with self._lock:
            state = self.state()
            before = state.model_dump()
            for name, report in reports.items():
                if isinstance(report, str):
                    state.errors[name] = report
                    continue
                state.errors.pop(name, None)
                _merge(state, name, *report)
            changed = state.model_dump() != before
            state.last_checked = self._now().isoformat(timespec="seconds")
            # Only a change moves the version on; the time of the last check is no news.
            _write_json(self._file, state)
            if changed:
                self._store._changed()

    # -- changes from the user -------------------------------------------------

    def _change(self, message_id: str, **fields: bool) -> Message:
        with self._lock:
            state = self.state()
            for index, message in enumerate(state.messages):
                if message.id == message_id:
                    state.messages[index] = message.model_copy(update=fields)
                    self._save(state)
                    return state.messages[index]
        raise MessageNotFound(message_id)

    def mark(self, message_id: str, unread: bool) -> None:
        message = self._change(message_id, unread=unread)
        # Sources only know how to mark read: unread stays a matter of bytearmy.
        if not unread:
            self._tell(message, "mark_read")

    def dismiss(self, message_id: str) -> None:
        message = self._change(message_id, dismissed=True, unread=False)
        self._tell(message, "dismiss")

    def _tell(self, message: Message, action: str) -> None:
        """Pass a change on to its source without keeping the window waiting."""
        source = self._sources.get(message.source)
        if not source:
            return

        def run() -> None:
            try:
                getattr(source, action)(message)
            except SourceFailed as error:
                with self._lock:
                    state = self.state()
                    state.errors[message.source] = str(error) or "failed"
                    self._save(state)

        worker = threading.Thread(target=run, name=f"inbox-{action}", daemon=True)
        self._workers = [alive for alive in self._workers if alive.is_alive()] + [worker]
        worker.start()


def _merge(state: InboxState, source: str, unread: list[Message], complete: bool) -> None:
    """Take a source's unread messages into the state."""
    known = {message.id: message for message in state.messages}
    for message in unread:
        old = known.get(message.id)
        if old and old.updated_at == message.updated_at:
            continue  # nothing new: what the user did with it stands
        known[message.id] = message  # new, or new activity: unread and shown again
    if complete:
        # A message the source no longer counts as unread was read elsewhere.
        reported = {message.id for message in unread}
        for message_id, message in known.items():
            if message.source == source and message.unread and message_id not in reported:
                known[message_id] = message.model_copy(update={"unread": False})
    ordered = sorted(known.values(), key=lambda message: message.updated_at, reverse=True)
    state.messages = ordered[:KEPT_MESSAGES]
