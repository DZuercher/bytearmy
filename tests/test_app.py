"""Checks for the entry point."""

from __future__ import annotations

from bytearmy.app import default_root, window_env


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
    }
