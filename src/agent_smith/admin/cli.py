"""Privileged CLI for bootstrapping and managing admin operators."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import socket
import sys
from collections.abc import Callable, Sequence

from agent_smith.admin.composition import AdminCliContainer, build_admin_cli_container
from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminBootstrapConflictError,
    AdminBootstrapRequiredError,
    AdminStoreConflictError,
    LastActiveAdminError,
)
from agent_smith.app.services.admin import (
    AdminOperatorNotFoundError,
    AdminValidationError,
)

Input = Callable[[str], str]
PasswordInput = Callable[[str], str]
ContainerFactory = Callable[[], AdminCliContainer]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-smith-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bootstrap-admin", "add-admin"):
        command = commands.add_parser(name)
        command.add_argument("--username")
        command.add_argument("--display-name")
    for name in ("reset-password", "disable-admin"):
        command = commands.add_parser(name)
        command.add_argument("--username")
    return parser


def _actor() -> AdminActorContext:
    return AdminActorContext(
        kind="admin_cli",
        identifier=f"{getpass.getuser()}@{socket.gethostname()}",
    )


def _required(value: str | None, prompt: str, input_fn: Input) -> str:
    return value if value is not None else input_fn(prompt)


def _confirmed_password(password_fn: PasswordInput) -> str:
    password = password_fn("Password: ")
    confirmation = password_fn("Confirm password: ")
    if password != confirmation:
        raise AdminValidationError("Password confirmation does not match.")
    return password


async def _run_command(
    args: argparse.Namespace,
    *,
    container: AdminCliContainer,
    input_fn: Input,
    password_fn: PasswordInput,
) -> str:
    username = _required(args.username, "Username: ", input_fn)
    actor = _actor()
    if args.command in {"bootstrap-admin", "add-admin"}:
        display_name = _required(args.display_name, "Display name: ", input_fn)
        password = _confirmed_password(password_fn)
        if args.command == "bootstrap-admin":
            operator = await container.operators.bootstrap_admin(
                username=username,
                display_name=display_name,
                password=password,
                actor=actor,
            )
        else:
            operator = await container.operators.add_admin(
                username=username,
                display_name=display_name,
                password=password,
                actor=actor,
            )
        return f"Admin operator {operator.username} ({operator.id}) created."
    if args.command == "reset-password":
        password = _confirmed_password(password_fn)
        operator = await container.operators.reset_password(
            username=username,
            password=password,
            actor=actor,
        )
        return f"Password reset and sessions revoked for {operator.username}."
    operator = await container.operators.disable_admin(username=username, actor=actor)
    return f"Admin operator {operator.username} disabled and sessions revoked."


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: Input = input,
    password_fn: PasswordInput | None = None,
    container_factory: ContainerFactory = build_admin_cli_container,
) -> int:
    args = _parser().parse_args(argv)
    resolved_password_fn = password_fn or getpass.getpass

    async def run() -> str:
        container = container_factory()
        try:
            return await _run_command(
                args,
                container=container,
                input_fn=input_fn,
                password_fn=resolved_password_fn,
            )
        finally:
            await container.close()

    try:
        message = asyncio.run(run())
    except (
        AdminBootstrapConflictError,
        AdminBootstrapRequiredError,
        AdminOperatorNotFoundError,
        AdminStoreConflictError,
        AdminValidationError,
        LastActiveAdminError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
