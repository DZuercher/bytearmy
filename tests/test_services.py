"""Checks for the programs without a terminal, against real processes."""

from __future__ import annotations

import os
import re
import signal
import sys
import threading

import httpx
import pytest
from conftest import FakeService, running, wait_until

from bytearmy import services as services_module
from bytearmy.models import Tool
from bytearmy.services import Services, launch
from bytearmy.tools import ToolFailed

ENV = {"PATH": os.environ["PATH"]}
WINDOW = Tool(id="window", name="Window", kind="external", command="unused")


@pytest.fixture
def services(tmp_path):
    made = Services(tmp_path / "logs")
    yield made
    made.shutdown()


def tool(fake: FakeService, **how) -> Tool:
    service = {"command": fake.command(**how), "url": fake.url}
    return Tool(id="fake", name="Fake", kind="web", url="{service}", service=service)


def failing(name: str, command: str) -> Tool:
    service = {"command": command, "url": "http://127.0.0.1:{port}/"}
    return Tool(id=name.lower(), name=name, kind="web", url="{service}", service=service)


def status(url: str) -> int | None:
    """The status a single GET gets, or None if nothing listens."""
    try:
        with httpx.Client(trust_env=False) as client:
            return client.get(url).status_code
    except httpx.TransportError:
        return None


# -- services ---------------------------------------------------------------


def test_a_service_gets_a_port_and_a_token_and_is_awaited(services, fake_service):
    url = services.url(tool(fake_service, preparing=2), ENV)

    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/\?tkn=[\w-]{16,}", url)
    assert status(url) == 302  # it knows the token, and "still preparing" is over
    assert set(fake_service.noted("requests")) == {"/"}  # nobody followed where it points to


def test_panes_that_ask_at_the_same_time_share_one_service(services, fake_service):
    fake = tool(fake_service, preparing=2)
    urls = []
    asking = [
        threading.Thread(target=lambda: urls.append(services.url(fake, ENV))) for _ in range(3)
    ]
    for thread in asking:
        thread.start()
    for thread in asking:
        thread.join()
    urls.append(services.url(fake, ENV))

    assert (len(urls), len(set(urls))) == (4, 1)
    assert len(fake_service.started()) == 1


def test_a_service_that_has_ended_is_started_again(services, fake_service):
    fake = tool(fake_service)
    first = services.url(fake, ENV)
    os.killpg(fake_service.started()[0][0], signal.SIGKILL)  # the service leads its group
    wait_until(lambda: status(first) is None, "the end of the service")

    second = services.url(fake, ENV)

    assert status(second) == 302
    assert len(fake_service.started()) == 2
    assert first.split("tkn=")[1] != second.split("tkn=")[1]  # nobody can guess the token


def test_a_service_that_cannot_start_reports_why(services, monkeypatch):
    monkeypatch.setattr(services_module, "STARTUP_SECONDS", 3)
    wordy = f"{sys.executable} -c \"import sys; sys.exit('x' * 600 + 'the end')\""

    with pytest.raises(ToolFailed) as failure:
        services.url(failing("Broken", "echo no such program >&2; exit 3"), ENV)
    with pytest.raises(ToolFailed) as long_failure:
        services.url(failing("Wordy", wordy), ENV)

    assert str(failure.value) == "Broken could not start (exit code 3). no such program"
    assert str(long_failure.value) == (
        "Wordy could not start (exit code 1). " + "x" * 493 + "the end"
    )


def test_a_service_that_never_answers_is_given_up_and_ended(services, tmp_path, monkeypatch):
    monkeypatch.setattr(services_module, "STARTUP_SECONDS", 0.5)
    silent = failing("Silent", f"echo $$ > {tmp_path / 'pid'}; exec sleep 60")

    with pytest.raises(ToolFailed) as failure:
        services.url(silent, ENV)

    assert str(failure.value) == "Silent did not answer within 0.5 seconds."
    assert not running(int((tmp_path / "pid").read_text()))


def test_the_proxy_of_the_environment_is_not_asked_for_a_service(
    services, fake_service, monkeypatch
):
    monkeypatch.setattr(services_module, "STARTUP_SECONDS", 3)
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    assert status(services.url(tool(fake_service), ENV)) == 302


def test_shutdown_ends_a_service_and_what_it_started(tmp_path, fake_service):
    services = Services(tmp_path / "logs")
    services.url(tool(fake_service), ENV)
    (pids,) = fake_service.started()

    services.shutdown()

    wait_until(lambda: not any(running(pid) for pid in pids), "the end of the service")
    with pytest.raises(ToolFailed, match="Fake was not started: bytearmy is shutting down"):
        services.url(tool(fake_service), ENV)
    assert len(fake_service.started()) == 1


def test_shutdown_asks_a_service_to_end_and_then_leaves_it_alone(
    tmp_path, fake_service, monkeypatch
):
    services = Services(tmp_path / "logs")
    services.url(tool(fake_service), ENV)
    sent = []
    send = os.killpg

    def watched(group: int, signal_number: int) -> None:
        sent.append(signal_number)
        send(group, signal_number)

    monkeypatch.setattr(services_module.os, "killpg", watched)

    services.shutdown()

    assert fake_service.noted("farewell") == ["asked to end"]
    assert sent == [signal.SIGTERM]  # after its end the process id may be someone else's


@pytest.mark.parametrize("gone", [ProcessLookupError, PermissionError])
def test_shutdown_tolerates_a_service_that_has_just_ended(
    tmp_path, fake_service, monkeypatch, gone
):
    services = Services(tmp_path / "logs")
    services.url(tool(fake_service), ENV)
    send = os.killpg

    def refuse(_group: int, _signal_number: int) -> None:
        raise gone

    monkeypatch.setattr(services_module.os, "killpg", refuse)
    try:
        services.shutdown()
    finally:
        send(fake_service.started()[0][0], signal.SIGKILL)


def test_shutdown_uses_force_on_a_service_that_ignores_the_request(
    tmp_path, fake_service, monkeypatch
):
    monkeypatch.setattr(services_module, "STOP_GRACE_SECONDS", 0.3)
    services = Services(tmp_path / "logs")
    services.url(tool(fake_service, before="trap '' TERM; "), ENV)
    (pids,) = fake_service.started()

    services.shutdown()

    wait_until(lambda: not any(running(pid) for pid in pids), "the end of the service")


# -- external tools ---------------------------------------------------------


def test_launch_runs_the_command_in_the_directory_with_the_environment(tmp_path):
    log = tmp_path / "logs" / "window.log"

    launch(WINDOW, 'echo "$MARK in $PWD" > launched', tmp_path, {**ENV, "MARK": "marked"}, log)

    assert (tmp_path / "launched").read_text() == f"marked in {tmp_path.resolve()}\n"


def test_launch_reports_a_program_that_fails_at_once(tmp_path):
    with pytest.raises(ToolFailed) as failure:
        launch(WINDOW, "echo broke >&2; exit 4", tmp_path, ENV, tmp_path / "window.log")

    assert str(failure.value) == "Window failed (exit code 4). broke"


def test_launch_leaves_a_program_running_that_does_not_end_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(services_module, "EXTERNAL_GRACE_SECONDS", 0.3)

    launch(WINDOW, "echo $$ > pid; exec sleep 60", tmp_path, ENV, tmp_path / "window.log")

    pid = int((tmp_path / "pid").read_text())
    try:
        assert running(pid)
    finally:
        os.kill(pid, signal.SIGKILL)
