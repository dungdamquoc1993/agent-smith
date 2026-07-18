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
        if module == "agent_smith.infra.storage" or module.startswith("agent_smith.infra.storage.")
    }
    assert violations == {}


def test_application_layer_uses_ports_not_infrastructure() -> None:
    violations = {
        str(path.relative_to(SRC)): module
        for path in (SRC / "app").rglob("*.py")
        for module in _imported_modules(path)
        if module == "sqlalchemy"
        or module.startswith("sqlalchemy.")
        or module == "agent_smith.infra"
        or module.startswith("agent_smith.infra.")
    }
    assert violations == {}


def test_admin_cli_does_not_import_runtime_composition_or_heavy_runtime_dependencies() -> None:
    forbidden = (
        "agent_smith.bootstrap.runtime_http",
        "agent_smith.core.llm",
        "agent_smith.infra.storage.s3",
        "agent_smith.transports.runtime_http",
        "agent_smith.workers",
    )
    violations = {
        str(path.relative_to(SRC)): module
        for path in (SRC / "admin").rglob("*.py")
        for module in _imported_modules(path)
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
    }
    assert violations == {}


def test_runtime_composition_does_not_construct_control_plane_capabilities() -> None:
    source = (SRC / "bootstrap" / "runtime_http.py").read_text(encoding="utf-8")
    modules = _imported_modules(SRC / "bootstrap" / "runtime_http.py")
    assert "agent_smith.app.services.admin" not in modules
    assert not any(module.startswith("agent_smith.admin") for module in modules)
    assert "IdentityProviderControl" not in source


def test_admin_http_does_not_import_runtime_llm_s3_or_workers() -> None:
    forbidden = (
        "agent_smith.bootstrap.runtime_http",
        "agent_smith.core.llm",
        "agent_smith.infra.storage.s3",
        "agent_smith.workers",
    )
    roots = [SRC / "bootstrap" / "admin_http.py", *(SRC / "transports" / "admin_http").glob("*.py")]
    violations = {
        str(path.relative_to(SRC)): module
        for path in roots
        for module in _imported_modules(path)
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
    }
    assert violations == {}


def test_legacy_runtime_admin_surface_is_absent() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in SRC.rglob("*.py")
    )
    assert "AGENT_SMITH_ADMIN_" + "TOKEN" not in sources
    assert "/api/" + "admin" not in sources
    assert "IdentityProvider" + "ManagementService" not in sources
    assert "PostgresIdentityProvider" + "AdminStore" not in sources


def test_document_worker_logic_does_not_import_composition_or_concrete_storage() -> None:
    forbidden = (
        "agent_smith.bootstrap",
        "agent_smith.transports.runtime_http",
        "agent_smith.infra.storage.postgres",
        "agent_smith.infra.storage.s3",
    )
    worker_root = SRC / "workers" / "document_processing"
    violations = {
        str(path.relative_to(SRC)): module
        for path in worker_root.glob("*.py")
        if path.name != "main.py"
        for module in _imported_modules(path)
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
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


def test_s3_sdk_is_confined_to_s3_storage_backend() -> None:
    allowed_root = SRC / "infra" / "storage" / "s3"
    violations = {
        str(path.relative_to(SRC)): module
        for path in SRC.rglob("*.py")
        if not path.is_relative_to(allowed_root)
        for module in _imported_modules(path)
        if module == "boto3"
        or module.startswith("boto3.")
        or module == "botocore"
        or module.startswith("botocore.")
    }
    assert violations == {}
