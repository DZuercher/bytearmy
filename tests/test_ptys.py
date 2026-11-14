"""Checks for programs in pseudo-terminals, against real ones."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest
from conftest import read_to_end, read_until

from bytearmy import ptys
from bytearmy.ptys import Pty


def start(tmp_path, command: str | None = None, rows: int = 24, cols: int = 80, **extra) -> Pty:
    """A shell, or a shell running one command, with its exit codes collected in `.ended`."""
    ended: list[int] = []
    argv = ["/bin/sh"] if command is None else ["/bin/sh", "-c", command]
    env = {"PATH": os.environ["PATH"], "TERM": "xterm-256color", "PS1": "$ ", **extra}
    pty = Pty(argv, tmp_path, env, rows, cols, on_exit=ended.append)
    pty.ended = ended
    return pty


def test_output_reaches_a_listener(tmp_path):
    async def scenario():
        pty = start(tmp_path, "echo out-$((40 + 2))")
        return await read_to_end(pty.listen()), pty

    seen, pty = asyncio.run(scenario())

    assert b"out-42" in seen
    assert pty.exit_code == 0


def test_the_end_is_announced_only_after_on_exit_has_run(tmp_path):
    reported: list[int] = []

    def slow_on_exit(code: int) -> None:
        time.sleep(0.3)
        reported.append(code)

    async def scenario():
        env = {"PATH": os.environ["PATH"]}
        pty = Pty(["/bin/sh", "-c", "exit 0"], tmp_path, env, 24, 80, on_exit=slow_on_exit)
        await read_to_end(pty.listen())
        return list(reported)

    assert asyncio.run(scenario()) == [0]


def test_input_reaches_the_program(tmp_path):
    async def scenario():
        pty = start(tmp_path, 'read word; echo "got-$word$word"')
        output = pty.listen()
        pty.write(b"ab\n")  # the terminal echoes "ab"; only the program prints "got-abab"
        await read_until(output, rb"got-abab")

    asyncio.run(scenario())


def test_the_program_starts_at_the_given_size_and_follows_a_resize(tmp_path):
    async def scenario():
        pty = start(tmp_path, "while read _; do stty size; done", rows=40, cols=120)
        output = pty.listen()
        pty.write(b"\n")
        await read_until(output, rb"\b40 120\b")
        pty.resize(30, 100)
        pty.write(b"\n")
        await read_until(output, rb"\b30 100\b")
        pty.kill()

    asyncio.run(scenario())


def test_the_terminal_is_the_controlling_terminal_of_the_program(tmp_path):
    # Not a shell: bash and zsh take a controlling terminal by themselves when they start.
    program = "print('ctty-' + str(40 + 2), file=open('/dev/tty', 'w'))"

    async def scenario():
        env = {"PATH": os.environ["PATH"]}
        pty = Pty(
            [sys.executable, "-c", program], tmp_path, env, 24, 80, on_exit=lambda _code: None
        )
        return await read_to_end(pty.listen())

    assert b"ctty-42" in asyncio.run(scenario())


def test_the_program_gets_its_directory_and_environment(tmp_path):
    async def scenario():
        pty = start(tmp_path, 'echo "$MARK in $PWD"', MARK="marked")
        return await read_to_end(pty.listen())

    assert f"marked in {tmp_path.resolve()}".encode() in asyncio.run(scenario())


def test_a_late_listener_gets_what_was_printed_before(tmp_path):
    async def scenario():
        pty = start(tmp_path, "echo early-$((40 + 2)); sleep 30")
        await read_until(pty.listen(), rb"early-42")
        late = pty.listen()
        first = await asyncio.wait_for(late.get(), timeout=5)
        pty.kill()
        return first

    assert b"early-42" in asyncio.run(scenario())


def test_a_listener_whose_event_loop_is_gone_does_not_stop_the_others(tmp_path):
    async def listen_and_leave(pty):
        await read_until(pty.listen(), rb"ready")  # never stops listening; its loop closes

    async def later(pty):
        output = pty.listen()
        pty.write(b"ab\n")
        await read_until(output, rb"later-abab")

    pty = start(tmp_path, 'echo ready; while read word; do echo "later-$word$word"; done')
    asyncio.run(listen_and_leave(pty))
    asyncio.run(later(pty))
    pty.kill()

    assert pty.exit_code is not None


def test_a_listener_that_stopped_gets_no_more_output(tmp_path):
    async def scenario():
        pty = start(tmp_path, 'echo ready; while read word; do echo "later-$word$word"; done')
        stopped = pty.listen()
        await read_until(stopped, rb"ready")
        pty.stop_listening(stopped)
        other = pty.listen()
        pty.write(b"ab\n")
        await read_until(other, rb"later-abab")
        pty.kill()
        return stopped.empty()

    assert asyncio.run(scenario())


def test_only_the_end_of_the_output_is_kept_for_late_listeners(tmp_path, monkeypatch):
    monkeypatch.setattr(ptys, "REPLAY_BYTES", 10)

    async def scenario():
        pty = start(tmp_path, "printf 0123456789abcdefghij; sleep 30")
        await read_until(pty.listen(), rb"j")
        late = pty.listen()
        first = await asyncio.wait_for(late.get(), timeout=5)
        pty.kill()
        return first

    assert asyncio.run(scenario()) == b"abcdefghij"


def test_the_exit_code_is_reported_and_a_listener_after_the_end_learns_it_at_once(tmp_path):
    async def scenario():
        pty = start(tmp_path, "echo bye; exit 3")
        await read_to_end(pty.listen())
        after = pty.listen()
        return pty, await read_to_end(after, timeout=1)

    pty, replayed = asyncio.run(scenario())

    assert pty.ended == [3]
    assert pty.exit_code == 3
    assert b"bye" in replayed


def test_kill_ends_the_program_and_waits_for_it(tmp_path):
    pty = start(tmp_path, "sleep 60")

    began = time.monotonic()
    pty.kill()

    assert time.monotonic() - began < 2
    assert pty.exit_code is not None
    assert pty.ended != [0]


def test_an_ended_program_is_left_alone(tmp_path, monkeypatch):
    pty = start(tmp_path, "sleep 60")
    pty.kill()
    signalled = []
    monkeypatch.setattr(ptys.os, "killpg", lambda *call: signalled.append(call))

    pty.write(b"too late\n")
    pty.resize(10, 10)
    pty.kill()

    assert signalled == []  # its process id may belong to another program by now


def test_kill_lets_the_program_end_by_itself_before_it_uses_force(tmp_path):
    async def scenario():
        farewell = 'trap "echo graceful > farewell; exit 0" HUP; echo ready; while true; do sleep 0.1; done'
        pty = start(tmp_path, farewell)
        await read_until(pty.listen(), rb"ready")
        return pty

    pty = asyncio.run(scenario())
    pty.kill()

    assert (tmp_path / "farewell").read_text() == "graceful\n"
    assert pty.exit_code == 0


@pytest.mark.parametrize("gone", [ProcessLookupError, PermissionError])
def test_kill_tolerates_a_program_that_has_just_ended(tmp_path, monkeypatch, gone):
    def refuse(_group, _signal):
        raise gone

    pty = start(tmp_path, "sleep 0.2")
    monkeypatch.setattr(ptys.os, "killpg", refuse)

    pty.kill()

    assert pty.exit_code == 0


def test_kill_uses_force_on_a_program_that_ignores_the_hang_up(tmp_path, monkeypatch):
    monkeypatch.setattr(ptys, "KILL_GRACE_SECONDS", 0.3)

    async def scenario():
        pty = start(tmp_path, 'trap "" HUP; echo ready; while true; do sleep 1; done')
        await read_until(pty.listen(), rb"ready")
        return pty

    pty = asyncio.run(scenario())
    pty.kill()

    assert pty.exit_code is not None
