"""Checks for the entry point."""

from __future__ import annotations

from bytearmy.app import default_root


def test_root_defaults_to_home_and_follows_the_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BYTEARMY_ROOT", raising=False)
    assert default_root() == tmp_path / "bytearmy"

    monkeypatch.setenv("BYTEARMY_ROOT", str(tmp_path / "elsewhere"))
    assert default_root() == tmp_path / "elsewhere"
