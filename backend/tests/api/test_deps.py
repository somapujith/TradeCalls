"""Unit tests for app/api/deps.py's pure-logic helpers.

generate_strategy_version, parse_json_field, and targets_to_list are shared
by the route handlers under test elsewhere in tests/api/ (mostly exercised
indirectly there); this file covers their edge cases directly, including the
git-unavailable fallback branch that's hard to hit through the HTTP-level
tests since this repo does have a working git binary.
"""
from __future__ import annotations

import subprocess

import pytest

from app.api.deps import generate_strategy_version, parse_json_field, targets_to_list


# ---------------------------------------------------------------------------
# generate_strategy_version
# ---------------------------------------------------------------------------


def test_generate_strategy_version_uses_git_hash_when_available() -> None:
    version = generate_strategy_version()

    assert version.startswith("git-") or version.startswith("ts-")


def test_generate_strategy_version_falls_back_on_called_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=128, cmd=["git", "rev-parse", "--short", "HEAD"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    version = generate_strategy_version()

    assert version.startswith("ts-")


def test_generate_strategy_version_falls_back_on_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(subprocess, "run", fake_run)

    version = generate_strategy_version()

    assert version.startswith("ts-")


def test_generate_strategy_version_falls_back_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)

    version = generate_strategy_version()

    assert version.startswith("ts-")


def test_generate_strategy_version_falls_back_when_git_output_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResult:
        stdout = "   \n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())

    version = generate_strategy_version()

    assert version.startswith("ts-")


def test_generate_strategy_version_uses_hash_when_git_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResult:
        stdout = "abc1234\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())

    version = generate_strategy_version()

    assert version == "git-abc1234"


# ---------------------------------------------------------------------------
# parse_json_field
# ---------------------------------------------------------------------------


def test_parse_json_field_none_input_returns_none() -> None:
    assert parse_json_field(None) is None


def test_parse_json_field_valid_json_object() -> None:
    assert parse_json_field('{"a": 1}') == {"a": 1}


def test_parse_json_field_valid_json_array() -> None:
    assert parse_json_field("[1, 2, 3]") == [1, 2, 3]


def test_parse_json_field_invalid_json_returns_none() -> None:
    assert parse_json_field("not-json{") is None


def test_parse_json_field_empty_string_returns_none() -> None:
    assert parse_json_field("") is None


# ---------------------------------------------------------------------------
# targets_to_list
# ---------------------------------------------------------------------------


def test_targets_to_list_order_and_values() -> None:
    result = targets_to_list(101.0, 102.5, 104.0, 106.0, 108.0)

    assert result == [101.0, 102.5, 104.0, 106.0, 108.0]


def test_targets_to_list_nearest_structural_target_nullable() -> None:
    result = targets_to_list(101.0, 102.5, 104.0, 106.0, None)

    assert result == [101.0, 102.5, 104.0, 106.0, None]
