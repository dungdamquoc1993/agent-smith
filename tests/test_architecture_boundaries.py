from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "agent_smith"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_core_does_not_depend_on_concrete_storage() -> None:
    violations = {
        str(path.relative_to(SRC)): module
        for path in (SRC / "core").rglob("*.py")
        for module in _imported_modules(path)
        if module == "agent_smith.infra.storage"
        or module.startswith("agent_smith.infra.storage.")
    }
    assert violations == {}


def test_application_services_use_ports_not_storage_implementations() -> None:
    violations = {
        str(path.relative_to(SRC)): module
        for path in (SRC / "app" / "services").rglob("*.py")
        for module in _imported_modules(path)
        if module == "sqlalchemy"
        or module.startswith("sqlalchemy.")
        or module == "agent_smith.infra"
        or module.startswith("agent_smith.infra.")
    }
    assert violations == {}


def test_sqlalchemy_is_confined_to_postgres_storage_backend() -> None:
    allowed_root = SRC / "infra" / "storage" / "postgres"
    violations = {
        str(path.relative_to(SRC)): module
        for path in (SRC / "infra").rglob("*.py")
        if not path.is_relative_to(allowed_root)
        for module in _imported_modules(path)
        if module == "sqlalchemy" or module.startswith("sqlalchemy.")
    }
    assert violations == {}
