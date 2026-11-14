"""Entry point: start the core, open the window, stop when the window closes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
from pathlib import Path

import uvicorn

from bytearmy.api import create_app
from bytearmy.store import Store

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
ROOT_VARIABLE = "BYTEARMY_ROOT"


def default_root() -> Path:
    return Path(os.environ.get(ROOT_VARIABLE) or Path.home() / "bytearmy")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def run(root: Path) -> None:
    token = secrets.token_urlsafe(24)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    app = create_app(Store(root), token, ui_dist=UI_DIR / "dist")
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
        env={**os.environ, "BYTEARMY_URL": url, "BYTEARMY_TOKEN": token},
    )
    window_closed = asyncio.create_task(electron.wait())
    await asyncio.wait({serving, window_closed}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    if electron.returncode is None:
        electron.terminate()
    await serving
    window_closed.cancel()


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
    args = parser.parse_args()
    if args.openapi:
        # The schema does not depend on any data, so no root is touched.
        print(json.dumps(create_app(store=None, token="").openapi(), indent=2))  # type: ignore[arg-type]
        return
    asyncio.run(run(args.root.expanduser().resolve()))
