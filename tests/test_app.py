"""Checks for the entry point."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys

import pytest

from bytearmy import app
from bytearmy.app import RootInUse, default_root, hold_root, main, window_env


def test_root_defaults_to_home_and_follows_the_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BYTEARMY_ROOT", raising=False)
    assert default_root() == tmp_path / "bytearmy"

    monkeypatch.setenv("BYTEARMY_ROOT", str(tmp_path / "elsewhere"))
    assert default_root() == tmp_path / "elsewhere"


def test_the_window_keeps_its_own_data_inside_the_root(tmp_path):
    assert window_env(tmp_path / "root", "http://127.0.0.1:1234", "secret") == {
        "BYTEARMY_URL": "http://127.0.0.1:1234",
        "BYTEARMY_TOKEN": "secret",
        "BYTEARMY_PROFILE": str(tmp_path / "root" / ".bytearmy" / "electron"),
        "BYTEARMY_WINDOW_LOG": str(tmp_path / "root" / ".bytearmy" / "logs" / "electron.log"),
    }


HOLD = """\
import sys, time
from pathlib import Path
from bytearmy.app import hold_root
claim = hold_root(Path(sys.argv[1]))
print("holding", flush=True)
time.sleep(60)
"""


def test_only_one_process_at_a_time_holds_a_root(tmp_path):
    root = tmp_path / "root"
    other = subprocess.Popen([sys.executable, "-c", HOLD, root], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline() == "holding\n"
        with pytest.raises(RootInUse) as refused:
            hold_root(root)
        assert refused.value.args == (root, str(other.pid))
        hold_root(tmp_path / "elsewhere").close()
    finally:
        other.kill()  # no chance to clean up: the system has to lift the claim
        other.wait()
        other.stdout.close()

    with hold_root(root), pytest.raises(RootInUse) as refused:
        hold_root(root)
    assert refused.value.args == (root, str(os.getpid()))


def test_a_second_bytearmy_on_a_root_says_which_process_has_it(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    ran = []

    async def run(given):
        with pytest.raises(RootInUse):  # the root is held while bytearmy runs
            hold_root(given)
        ran.append(given)

    monkeypatch.setattr(app, "run", run)
    monkeypatch.setattr(sys, "argv", ["bytearmy", "--root", str(root)])
    monkeypatch.setattr("builtins.input", lambda question: pytest.fail(f"asked {question!r}"))
    main()
    assert ran == [root]

    with hold_root(root), pytest.raises(SystemExit) as refused:
        main()
    assert refused.value.code == (
        f"bytearmy already runs on {root} (process {os.getpid()}). Close its window first."
    )
    # Held by a process that has not written its id yet.
    with open(root / ".bytearmy" / "lock", "w") as unnamed:
        fcntl.flock(unnamed, fcntl.LOCK_EX)
        with pytest.raises(SystemExit) as refused:
            main()
    assert refused.value.code == f"bytearmy already runs on {root}. Close its window first."
    assert ran == [root]


@pytest.mark.parametrize(
    ("answer", "starts"),
    [("y", True), (" Yes ", True), ("", False), ("n", False), (EOFError, False)],
)
def test_a_root_that_does_not_exist_is_created_only_when_the_user_says_so(
    tmp_path, monkeypatch, answer, starts
):
    root = (tmp_path / "bytearmy-check,").resolve()  # a typo
    asked, ran = [], []

    def reply(question):
        asked.append(question)
        if answer is EOFError:  # no terminal to ask on
            raise EOFError
        return answer

    async def run(given):
        ran.append(given)

    monkeypatch.setattr("builtins.input", reply)
    monkeypatch.setattr(app, "run", run)
    monkeypatch.setattr(sys, "argv", ["bytearmy", "--root", str(root)])

    if starts:
        main()
    else:
        with pytest.raises(SystemExit) as refused:
            main()
        assert refused.value.code == f"bytearmy did not start: {root} does not exist."
    assert asked == [f"{root} does not exist. Create it? [y/N] "]
    assert (ran, root.exists()) == (([root], True) if starts else ([], False))
