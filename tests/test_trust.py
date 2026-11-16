"""Checks for trusting directories in Claude's configuration."""

from __future__ import annotations

import json
from pathlib import Path

from bytearmy.trust import claude_config as config_of
from bytearmy.trust import trust_directory


def trusted(config: Path) -> dict[str, bool]:
    projects = json.loads(config.read_text())["projects"]
    return {path: entry.get("hasTrustDialogAccepted", False) for path, entry in projects.items()}


def test_the_config_lies_in_the_home_or_in_claudes_config_directory(tmp_path):
    assert config_of({}) == Path.home() / ".claude.json"
    assert config_of({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path / ".claude.json"


def test_without_a_config_given_the_one_of_the_environment_is_used(tmp_path, claude_config):
    trust_directory(tmp_path)
    assert trusted(claude_config) == {str(tmp_path.resolve()): True}


def test_a_directory_is_trusted_in_a_new_config(tmp_path):
    config = tmp_path / "config" / ".claude.json"
    trust_directory(tmp_path / "task", config)
    assert trusted(config) == {str((tmp_path / "task").resolve()): True}


def test_trusting_keeps_everything_else_in_the_config(tmp_path):
    config = tmp_path / ".claude.json"
    other = {"theme": "dark", "projects": {"/elsewhere": {"allowedTools": ["Bash"]}}}
    config.write_text(json.dumps(other))
    config.chmod(0o600)
    directory = tmp_path / "task"
    directory.mkdir()

    trust_directory(directory, config)

    settings = json.loads(config.read_text())
    assert settings["theme"] == "dark"
    assert settings["projects"]["/elsewhere"] == {"allowedTools": ["Bash"]}
    assert settings["projects"][str(directory.resolve())] == {"hasTrustDialogAccepted": True}
    assert config.stat().st_mode & 0o777 == 0o600


def test_an_entry_of_the_directory_keeps_its_other_settings(tmp_path):
    config = tmp_path / ".claude.json"
    directory = str(tmp_path.resolve())
    config.write_text(json.dumps({"projects": {directory: {"allowedTools": ["Bash"]}}}))

    trust_directory(tmp_path, config)

    assert json.loads(config.read_text())["projects"][directory] == {
        "allowedTools": ["Bash"],
        "hasTrustDialogAccepted": True,
    }


def test_a_trusted_directory_leaves_the_file_alone(tmp_path):
    config = tmp_path / ".claude.json"
    text = json.dumps({"projects": {str(tmp_path.resolve()): {"hasTrustDialogAccepted": True}}})
    config.write_text(text)

    trust_directory(tmp_path, config)

    assert config.read_text() == text


def test_a_directory_below_a_trusted_parent_gets_an_entry_of_its_own(tmp_path):
    # Claude asks in a git repository even when a parent of it is trusted.
    config = tmp_path / ".claude.json"
    parent = str(tmp_path.resolve())
    config.write_text(json.dumps({"projects": {parent: {"hasTrustDialogAccepted": True}}}))
    repo = tmp_path / "task" / "repo"

    trust_directory(repo, config)

    assert trusted(config) == {parent: True, str(repo.resolve()): True}


def test_a_config_that_cannot_be_read_is_left_alone(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text("{ not json")

    trust_directory(tmp_path, config)

    assert config.read_text() == "{ not json"
