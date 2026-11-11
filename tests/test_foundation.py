from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

import async_orm_kit
from async_orm_kit import Manager, Model, ORMConfig, QueryCompiler, StringField
from async_orm_kit._logging import get_logger
from async_orm_kit.models.fields.typed.file import FileField
from async_orm_kit.queryset.state import QueryState


class _User(Model):
    __tablename__ = "foundation_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(StringField(max_length=32))


class _ConfiguredUser(Model):
    __tablename__ = "configured_foundation_users"

    objects = Manager(config=ORMConfig(max_joinedload_depth=3))
    id: Mapped[int] = mapped_column(primary_key=True)


def test_public_api_is_importable() -> None:
    assert async_orm_kit.Model is Model
    assert async_orm_kit.Manager is Manager
    assert async_orm_kit.QueryCompiler is QueryCompiler
    assert async_orm_kit.StringField is StringField
    assert async_orm_kit.FileField is FileField
    assert async_orm_kit.AsyncStorageKitAdapter.__name__ == "AsyncStorageKitAdapter"
    assert async_orm_kit.__version__ == "1.0.0"


def test_orm_config_is_immutable_and_validated() -> None:
    config = ORMConfig(max_joinedload_depth=4)
    assert config.max_joinedload_depth == 4

    with pytest.raises(ValueError, match="at least 1"):
        ORMConfig(max_joinedload_depth=0)

    with pytest.raises(TypeError, match="must be an integer"):
        ORMConfig(max_joinedload_depth=True)  # type: ignore[arg-type]

    with pytest.raises((AttributeError, TypeError)):
        config.max_joinedload_depth = 5  # type: ignore[misc]


def test_query_compiler_uses_explicit_configuration() -> None:
    compiler = QueryCompiler(ORMConfig(max_joinedload_depth=5))
    assert compiler.config.max_joinedload_depth == 5


def test_model_declares_default_manager() -> None:
    assert isinstance(_User.objects, Manager)
    assert _User.get_default_manager() is _User.objects
    assert _User.__tablename__ == "foundation_users"
    assert "nickname" in _User._model_field_types


def test_manager_can_configure_its_query_compiler() -> None:
    queryset = _ConfiguredUser.objects.get_queryset(object())
    assert queryset._compiler.config.max_joinedload_depth == 3


def test_string_field_constructs_and_validates() -> None:
    field = StringField(max_length=5, min_length=2)
    assert field.max_length == 5
    assert field.min_length == 2
    field.run_validators("okay")

    with pytest.raises(ValueError):
        field.run_validators("too-long")


def test_file_field_has_no_private_global_storage_dependency() -> None:
    field = FileField()
    with pytest.raises(RuntimeError, match="explicitly configured storage"):
        field.generate_filename(None, "avatar.png")


def test_package_has_no_imports_from_private_core_namespace() -> None:
    package_root = Path(async_orm_kit.__file__).parent
    for source in package_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert all(not name.startswith("core") for name in imported), (
                f"Private core import found in {source}: {imported}"
            )


def test_no_interactive_debugger_remains_in_bulk_writer() -> None:
    source = (Path(async_orm_kit.__file__).parent / "queryset" / "writer.py").read_text(
        encoding="utf-8"
    )
    assert "breakpoint(" not in source
    assert "pdb.set_trace(" not in source


def test_default_query_state_compiles_to_select_statement() -> None:
    from sqlalchemy.sql import Select

    statement = QueryCompiler().compile(QueryState(_User))
    assert isinstance(statement, Select)


def test_stdlib_logger_preserves_structured_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = get_logger("async_orm_kit.tests.logging")
    with caplog.at_level("INFO", logger="async_orm_kit.tests.logging"):
        logger.info("query_compiled", dialect="sqlite", query_count=1)

    record = caplog.records[-1]
    assert record.message == "query_compiled"
    assert record.orm_context == {"dialect": "sqlite", "query_count": 1}


def test_all_public_package_modules_are_importable() -> None:
    import importlib
    import pkgutil

    modules = list(
        pkgutil.walk_packages(async_orm_kit.__path__, async_orm_kit.__name__ + ".")
    )
    assert modules
    for module in modules:
        importlib.import_module(module.name)


def test_project_metadata_version_matches_runtime_version() -> None:
    import tomllib

    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == async_orm_kit.__version__


def test_core_import_does_not_load_optional_integrations() -> None:
    package_root = Path(async_orm_kit.__file__).parent
    source_root = package_root.parents[0]
    script = (
        "import sys; import async_orm_kit; "
        "assert 'alembic' not in sys.modules; "
        "assert 'fastapi' not in sys.modules; "
        "assert 'async_storage_kit' not in sys.modules"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_optional_integration_extras_do_not_become_runtime_dependencies() -> None:
    import tomllib

    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text())
    assert metadata["project"]["dependencies"] == ["SQLAlchemy[asyncio]>=2.0.40,<3.0"]
    extras = metadata["project"]["optional-dependencies"]
    assert "alembic>=1.13,<2.0" in extras["migrations"]
    assert "fastapi>=0.110,<1.0" in extras["fastapi"]
