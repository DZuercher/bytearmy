"""Entry point: start the core, open the window, stop when the window closes."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import secrets
import sys
from pathlib import Path
from typing import IO

import uvicorn

from bytearmy import gmail
from bytearmy.api import create_app, schema
from bytearmy.config import CONFIG_FILE, Config, config_of, ensure_config
from bytearmy.inbox import GitHubSource, Inbox
from bytearmy.orchestrator import Orchestrator
from bytearmy.services import free_port
from bytearmy.store import METADATA_DIR, Store
from bytearmy.workspaces import Workspaces

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
ROOT_VARIABLE = "BYTEARMY_ROOT"


class RootInUse(Exception):
    """args: the root, then the process id of the bytearmy that has it ("" if unknown)."""


def default_root() -> Path:
    return Path(os.environ.get(ROOT_VARIABLE) or Path.home() / "bytearmy")


def confirmed(question: str) -> bool:
    """Ask on the terminal. Anything but yes is no, and so is having no terminal to ask on."""
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def hold_root(root: Path) -> IO[str]:
    """Claim the root for this process; the claim lasts while the returned file is open.

    Two bytearmys on one root would run every pane twice. The claim is a lock that the
    system lifts when the process ends, also by a crash; the file names the process.
    """
    (root / METADATA_DIR).mkdir(parents=True, exist_ok=True)
    # Opened for appending: a failed attempt leaves the process id of the holder in place.
    file = open(root / METADATA_DIR / "lock", "a+")  # noqa: SIM115 -- the caller closes it
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        file.seek(0)
        holder = file.read().strip()
        file.close()
        raise RootInUse(root, holder) from None
    file.truncate(0)
    file.write(str(os.getpid()))
    file.flush()
    return file


def window_env(root: Path, url: str, token: str) -> dict[str, str]:
    """What the window is told: where the core is, and where it keeps its own data."""
    return {
        "BYTEARMY_URL": url,
        "BYTEARMY_TOKEN": token,
        # Logins made in web panes; inside the root, so every root has its own.
        "BYTEARMY_PROFILE": str(root / METADATA_DIR / "electron"),
        # Errors of the page and crashes of its processes, which leave no trace otherwise.
        "BYTEARMY_WINDOW_LOG": str(root / METADATA_DIR / "logs" / "electron.log"),
    }


async def run(root: Path) -> None:
    token = secrets.token_urlsafe(24)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    store = Store(root)
    workspaces = Workspaces(store)
    orchestrator = Orchestrator(store, workspaces, autostart=True)
    ensure_config(root)

    def settings() -> Config:
        return config_of(root)

    inbox = Inbox(
        store,
        [GitHubSource(settings=settings), *gmail.sources(root, settings)],
        on_new=orchestrator.announce,
        settings=settings,
    )
    app = create_app(
        store,
        token,
        ui_dist=UI_DIR / "dist",
        url=url,
        inbox=inbox,
        workspaces=workspaces,
        orchestrator=orchestrator,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3
        )
    )
    serving = asyncio.create_task(server.serve())
    while not server.started and not serving.done():
        await asyncio.sleep(0.05)
    if serving.done():
        await serving
        return

    electron = await asyncio.create_subprocess_exec(
        str(UI_DIR / "node_modules" / ".bin" / "electron"),
        str(UI_DIR),
        env={**os.environ, **window_env(root, url, token)},
    )
    window_closed = asyncio.create_task(electron.wait())
    await asyncio.wait({serving, window_closed}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    if electron.returncode is None:
        electron.terminate()
    await serving
    window_closed.cancel()


def gmail_login(root: Path, address: str) -> None:
    """Log in to a Gmail account the config names; bytearmy may run meanwhile."""
    accounts = config_of(root).inbox.gmail.accounts
    account = next((account for account in accounts if account.address == address), None)
    if account is None:
        sys.exit(f"{address} is no account of [inbox.gmail] in {root / CONFIG_FILE}.")
    try:
        file = gmail.login(root, account)
    except gmail.SourceFailed as error:
        sys.exit(f"Logging in to {address} failed: {error}")
    print(f"Kept the secrets of {address} in {file}. bytearmy asks it from its next start.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="bytearmy", description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help=f"directory that holds all projects (default: ${ROOT_VARIABLE} or ~/bytearmy)",
    )
    parser.add_argument(
        "--openapi", action="store_true", help="print the API schema as JSON and exit"
    )
    parser.add_argument(
        "--gmail-login",
        metavar="ADDRESS",
        help="ask for the secrets of a Gmail account of [inbox.gmail] and keep them, then exit",
    )
    args = parser.parse_args()
    if args.openapi:
        print(json.dumps(schema(), indent=2))
        return
    root = args.root.expanduser().resolve()
    if args.gmail_login:
        gmail_login(root, args.gmail_login)
        return
    # A mistyped root would otherwise start an empty bytearmy beside the real one.
    if not root.exists() and not confirmed(f"{root} does not exist. Create it?"):
        sys.exit(f"bytearmy did not start: {root} does not exist.")
    try:
        claim = hold_root(root)
    except RootInUse as error:
        holder = error.args[1]
        by = f" (process {holder})" if holder else ""
        sys.exit(f"bytearmy already runs on {root}{by}. Close its window first.")
    with claim:
        asyncio.run(run(root))
