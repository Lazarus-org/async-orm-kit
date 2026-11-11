"""Regression checks for copied package metadata and release automation."""

from __future__ import annotations

import ast
import re
import runpy
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_and_version_consistent():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    runtime = (ROOT / "src/async_orm_kit/_version.py").read_text()
    assert project["name"] == "async-orm-kit"
    assert project["version"] == "1.0.0"
    assert re.search(r'^__version__ = "1\.0\.0"$', runtime, re.M)
    assert project["requires-python"] == ">=3.11,<3.16"
    assert project["dependencies"] == ["SQLAlchemy[asyncio]>=2.0.40,<3.0"]
    assert "async-storage-kit" not in " ".join(project["dependencies"])


def test_optional_dependency_groups_are_separate():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = root["project"]["optional-dependencies"]
    assert {
        "sqlite",
        "postgres",
        "mysql",
        "storage",
        "migrations",
        "fastapi",
        "docs",
        "test",
    } <= extras.keys()
    assert {"quality", "test", "build", "dev"} <= root["dependency-groups"].keys()
    assert root["tool"]["uv"]["build-backend"]["module-name"] == "async_orm_kit"


def test_no_other_package_names_in_active_automation_and_policies():
    files = [
        ROOT / "pyproject.toml",
        ROOT / "tox.ini",
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CODE_OF_CONDUCT.md",
        *(ROOT / ".github").rglob("*.yml"),
        *(ROOT / ".github/scripts").rglob("*.py"),
    ]
    for file in files:
        if "__pycache__" in file.parts:
            continue
        text = file.read_text(encoding="utf-8")
        assert "fastapi_settings" not in text, file
        assert "fastapi-settings" not in text, file


def test_policy_sections_preserved():
    expected = {"CONTRIBUTING.md": 26, "CODE_OF_CONDUCT.md": 28}
    for filename, count in expected.items():
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert (
            len([line for line in text.splitlines() if line.startswith("##")]) == count
        )
        assert len(text.splitlines()) >= 340


def test_validator_is_safe_against_archive_traversal():
    validator = runpy.run_path(str(ROOT / ".github/scripts/validate_distribution.py"))
    check = validator["_safe_path"]
    for path in ("../secret", "/absolute", "a\\b", "pkg/__pycache__/x.pyc", "pkg/.env"):
        with pytest.raises(RuntimeError):
            check(path)
    assert check("async_orm_kit/models/base.py").parts[-1] == "base.py"


def test_automation_scripts_compile_without_importing_optional_drivers():
    for file in (ROOT / ".github/scripts").glob("*.py"):
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))


def test_workflows_verify_lock_and_require_live_database_evidence():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "uv lock --check --offline" in ci
    assert "mysql:8.4" in ci and "postgres:16-alpine" in ci
    assert "check_pytest_junit.py" in ci
    assert "tests/integration/test_advanced_mysql_async.py" in ci
    assert "tests/integration/test_advanced_postgres_async.py" in ci
    assert "tests/integration/test_advanced_sql_async.py" in ci
    assert "--min-tests 16" in ci and "--min-tests 32" in ci
    assert "tests/integration/test_advanced_stage7_async.py" in ci
    assert "--release" in release
    assert "resolve_release.py" in release
    assert "sha256sum --check SHA256SUMS" in release
    assert release.count("id-token: write") == 1


def test_stable_release_license_and_lock_metadata():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").read_text().startswith("MIT License\n")
    assert (ROOT / "uv.lock").is_file()
