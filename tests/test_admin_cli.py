from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_smith.admin.cli import main
from agent_smith.admin.config import AdminHttpSettings
from agent_smith.app.ports.admin import AdminBootstrapConflictError, AdminOperatorRecord


def _operator() -> AdminOperatorRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AdminOperatorRecord(
        id="operator-id",
        username="admin",
        display_name="Admin",
        password_hash="never-print-this-hash",
        status="active",
        password_changed_at=now,
    )


def test_cli_reads_password_twice_never_prints_it_and_closes_once(capsys) -> None:
    operators = SimpleNamespace(bootstrap_admin=AsyncMock(return_value=_operator()))
    container = SimpleNamespace(operators=operators, close=AsyncMock())
    prompts: list[str] = []

    result = main(
        ["bootstrap-admin", "--username", "admin", "--display-name", "Admin"],
        password_fn=lambda prompt: prompts.append(prompt) or "top-secret-password",
        container_factory=lambda: container,
    )

    assert result == 0
    assert prompts == ["Password: ", "Confirm password: "]
    operators.bootstrap_admin.assert_awaited_once()
    container.close.assert_awaited_once()
    output = capsys.readouterr()
    assert "top-secret-password" not in output.out + output.err
    assert "never-print-this-hash" not in output.out + output.err


def test_cli_password_mismatch_fails_safely_and_closes(capsys) -> None:
    operators = SimpleNamespace(bootstrap_admin=AsyncMock())
    container = SimpleNamespace(operators=operators, close=AsyncMock())
    values = iter(("password-one", "password-two"))

    result = main(
        ["bootstrap-admin", "--username", "admin", "--display-name", "Admin"],
        password_fn=lambda _: next(values),
        container_factory=lambda: container,
    )

    assert result == 1
    operators.bootstrap_admin.assert_not_awaited()
    container.close.assert_awaited_once()
    output = capsys.readouterr()
    assert "password-one" not in output.err
    assert "password-two" not in output.err


def test_second_bootstrap_is_rejected_and_container_still_closes(capsys) -> None:
    operators = SimpleNamespace(
        bootstrap_admin=AsyncMock(
            side_effect=AdminBootstrapConflictError("An admin operator already exists.")
        )
    )
    container = SimpleNamespace(operators=operators, close=AsyncMock())

    result = main(
        ["bootstrap-admin", "--username", "admin", "--display-name", "Admin"],
        password_fn=lambda _: "top-secret-password",
        container_factory=lambda: container,
    )

    assert result == 1
    container.close.assert_awaited_once()
    output = capsys.readouterr()
    assert "already exists" in output.err
    assert "top-secret-password" not in output.err


def test_add_reset_and_disable_commands_call_only_expected_services(capsys) -> None:
    for command, method_name, password_calls in (
        ("add-admin", "add_admin", 2),
        ("reset-password", "reset_password", 2),
        ("disable-admin", "disable_admin", 0),
    ):
        method = AsyncMock(return_value=_operator())
        operators = SimpleNamespace(**{method_name: method})
        container = SimpleNamespace(operators=operators, close=AsyncMock())
        prompts: list[str] = []
        arguments = [command, "--username", "admin"]
        if command == "add-admin":
            arguments.extend(["--display-name", "Admin"])

        result = main(
            arguments,
            password_fn=lambda prompt: prompts.append(prompt) or "top-secret-password",
            container_factory=lambda: container,
        )

        assert result == 0
        assert method.await_count == 1
        assert len(prompts) == password_calls
        container.close.assert_awaited_once()
    output = capsys.readouterr()
    assert "top-secret-password" not in output.out + output.err


def test_admin_settings_use_dedicated_database_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SMITH_POSTGRES_URL", "postgresql+asyncpg://runtime/db")
    monkeypatch.setenv("AGENT_SMITH_ADMIN_POSTGRES_URL", "postgresql+asyncpg://admin/db")

    settings = AdminHttpSettings(_env_file=None)

    assert settings.postgres_url == "postgresql+asyncpg://admin/db"
