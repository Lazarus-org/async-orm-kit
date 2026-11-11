"""Validate Async ORM Kit wheel/sdist metadata, contents and isolated installs.

Invoke after building exactly one wheel and one sdist. The `--release` flag
adds fail-closed legal/lockfile/version checks; it must be used for publishing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "async_orm_kit"
DIST_NAME = "async-orm-kit"
FORBIDDEN = {
    "_build",
    ".validation",
    ".release-venv",
    ".git",
    ".venv",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "build",
    "venv",
}
REQUIRED_SOURCE = {
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "CONTRIBUTORS.md",
    "AUTHORS.md",
    "tox.ini",
    ".readthedocs.yaml",
    "docs/index.rst",
    "docs/conf.py",
    ".pre-commit-config.yaml",
    ".github/dependabot.yml",
}


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or set(path.parts) & FORBIDDEN
        or path.name.startswith(".env")
        or path.suffix in {".pyc", ".pyo"}
    ):
        raise RuntimeError(f"Unsafe archive entry: {name}")
    return path


def _metadata(contents: dict[str, bytes], *, sdist: bool = False):
    matches = (
        [k for k in contents if k == "PKG-INFO"]
        if sdist
        else [k for k in contents if k.endswith(".dist-info/METADATA")]
    )
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one distribution metadata record")
    return BytesParser().parsebytes(contents[matches[0]])


def _check_metadata(meta, project: dict, release: bool):
    if meta.get("Name", "").lower().replace("_", "-") != DIST_NAME:
        raise RuntimeError("Distribution name does not match async-orm-kit")
    for key, expected in (
        ("Version", project["version"]),
        ("Requires-Python", project["requires-python"]),
    ):
        actual = meta.get(key)
        if key == "Requires-Python":
            matches = actual is not None and SpecifierSet(actual) == SpecifierSet(
                expected
            )
        else:
            matches = actual == expected
        if not matches:
            raise RuntimeError(f"{key} differs from pyproject.toml")
    if (
        meta.get("Description-Content-Type", "").split(";")[0].strip()
        != "text/markdown"
    ):
        raise RuntimeError("Missing Markdown description content type")
    if (
        "Programming Language :: Python :: Implementation :: CPython"
        not in meta.get_all("Classifier", [])
    ):
        raise RuntimeError("CPython classifier missing")

    installed_extras = {
        value.strip().lower().replace("_", "-")
        for value in meta.get_all("Provides-Extra", [])
    }
    configured_extras = set(project["optional-dependencies"])
    if installed_extras != configured_extras:
        raise RuntimeError("Published extras differ from pyproject.toml")
    mandatory = []
    for requirement in meta.get_all("Requires-Dist", []):
        if not requirement.partition(";")[2].strip():
            mandatory.append(requirement)
    if len(mandatory) != 1 or not re.match(
        r"(?i)^sqlalchemy(?:\[asyncio\])?\s*[<>=]", mandatory[0]
    ):
        raise RuntimeError(f"Expected mandatory SQLAlchemy only, found {mandatory!r}")
    if "async-storage-kit" in "\n".join(mandatory).lower():
        raise RuntimeError("Storage dependency leaked into mandatory requirements")
    if meta.get("License-Expression") != project.get("license"):
        raise RuntimeError("License expression differs from pyproject.toml")
    if "LICENSE" not in meta.get_all("License-File", []):
        raise RuntimeError("LICENSE is missing from distribution metadata")
    if release:
        if Version(project["version"]).is_prerelease:
            raise RuntimeError(
                "Do not publish a development version via the stable release workflow"
            )
        if not (ROOT / "LICENSE").is_file():
            raise RuntimeError(
                "Maintainer-approved LICENSE is required for public release"
            )
        if not (ROOT / "uv.lock").is_file():
            raise RuntimeError(
                "An approved and reproducible uv.lock is required for release"
            )
        if "LICENSE" not in meta.get_all("License-File", []):
            raise RuntimeError("LICENSE is not included in distribution metadata")
        if not project.get("urls"):
            raise RuntimeError(
                "Verified project URLs must be configured before release"
            )


def _source_required() -> set[str]:
    required = set(REQUIRED_SOURCE)
    for directory, suffixes in (
        ("src", {".py", ".pyi", ".typed"}),
        ("tests", {".py"}),
        ("docs", {".md", ".rst", ".py"}),
        (".github/scripts", {".py"}),
        (".github/workflows", {".yml", ".yaml"}),
    ):
        base = ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(ROOT)
            if (
                path.is_file()
                and not (set(rel.parts) & FORBIDDEN)
                and path.suffix in suffixes
            ):
                required.add(rel.as_posix())
    if (ROOT / "uv.lock").is_file():
        required.add("uv.lock")
    if (ROOT / "LICENSE").is_file():
        required.add("LICENSE")
    return required


def _read_wheel(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zip_file:
        names = zip_file.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate wheel file")
        for name in names:
            _safe_path(name)
        data = {
            member.filename: zip_file.read(member)
            for member in zip_file.infolist()
            if not member.is_dir()
        }
    licenses = [
        value
        for name, value in data.items()
        if name.endswith(".dist-info/licenses/LICENSE")
    ]
    if licenses != [(ROOT / "LICENSE").read_bytes()]:
        raise RuntimeError("Wheel must include the approved LICENSE text")
    if f"{PACKAGE}/py.typed" not in data:
        raise RuntimeError("Wheel missing py.typed marker")
    source_files = {
        path.relative_to(ROOT / "src").as_posix(): path.read_bytes()
        for path in (ROOT / "src" / PACKAGE).rglob("*")
        if path.is_file() and path.suffix in {".py", ".pyi", ".typed"}
    }
    for filename, contents in source_files.items():
        if data.get(filename) != contents:
            raise RuntimeError(f"Wheel runtime file missing or different: {filename}")
    for filename in data:
        if filename.startswith(f"{PACKAGE}/") or ".dist-info/" in filename:
            continue
        raise RuntimeError(f"Unexpected wheel file: {filename}")
    return data


def _read_sdist(path: Path) -> dict[str, bytes]:
    with tarfile.open(path) as tar:
        members = tar.getmembers()
        roots = set()
        data = {}
        for member in members:
            member_path = _safe_path(member.name)
            roots.add(member_path.parts[0])
            if member.isdir():
                continue
            if not member.isfile() or len(member_path.parts) < 2:
                raise RuntimeError(f"Unsafe sdist entry type: {member.name}")
            rel = PurePosixPath(*member_path.parts[1:]).as_posix()
            if rel in data:
                raise RuntimeError(f"Duplicate sdist path: {rel}")
            file_obj = tar.extractfile(member)
            if file_obj is None:
                raise RuntimeError(f"Unreadable sdist entry: {rel}")
            with file_obj:
                data[rel] = file_obj.read()
    if len(roots) != 1:
        raise RuntimeError("Source archive must have exactly one root")
    missing = (_source_required() | {"PKG-INFO"}) - data.keys()
    if missing:
        raise RuntimeError(f"Source archive missing files: {sorted(missing)}")
    for rel in _source_required():
        if rel == "pyproject.toml":
            if tomllib.loads(data[rel].decode()) != tomllib.loads(
                (ROOT / rel).read_text()
            ):
                raise RuntimeError("Source archive project metadata differs")
        elif data[rel] != (ROOT / rel).read_bytes():
            raise RuntimeError(f"Source archive file differs: {rel}")
    return data


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: int = 240,
):
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


def _test_source(files: dict[str, bytes], wheel_files: dict[str, bytes], uv: str):
    with tempfile.TemporaryDirectory(prefix="async-orm-kit-sdist-") as temp:
        root = Path(temp)
        source = root / "source"
        for rel, content in files.items():
            file = source.joinpath(*PurePosixPath(rel).parts)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        env = dict(
            os.environ, PYTHONPATH=str(source / "src"), PYTHONDONTWRITEBYTECODE="1"
        )
        _run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=source,
            env=env,
            timeout=600,
        )
        rebuilt = root / "rebuilt"
        _run(
            [
                uv,
                "build",
                "--no-sources",
                "--wheel",
                "--python",
                sys.executable,
                "--out-dir",
                str(rebuilt),
            ],
            cwd=source,
            env=env,
        )
        wheels = list(rebuilt.glob("*.whl"))
        if len(wheels) != 1 or _wheel_contents_only(wheels[0]) != wheel_files:
            raise RuntimeError("Rebuilt wheel content differs from validated wheel")


def _wheel_contents_only(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def _test_install(wheel: Path, uv: str, extras: bool):
    candidates = [None]
    if extras:
        candidates += list(
            tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
                "optional-dependencies"
            ]
        )
    with tempfile.TemporaryDirectory(prefix="async-orm-kit-install-") as temp:
        for extra in candidates:
            root = Path(temp) / (extra or "core")
            venv = root / "venv"
            _run([uv, "venv", "--python", sys.executable, str(venv)])
            py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            requirement = str(wheel) + (f"[{extra}]" if extra else "")
            _run(
                [
                    uv,
                    "pip",
                    "install",
                    "--link-mode",
                    "copy",
                    "--python",
                    str(py),
                    requirement,
                ]
            )
            _run([uv, "pip", "check", "--python", str(py)])
            code = (
                "from importlib.metadata import version; from async_orm_kit import Model, Manager; "
                "assert Model.metadata is not None; assert Manager is not None; "
                f"assert version('async-orm-kit') == {tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']!r}"
            )
            if extra is None:
                code += (
                    "; import sys; assert 'fastapi' not in sys.modules"
                    "; assert 'alembic' not in sys.modules"
                    "; assert 'async_storage_kit' not in sys.modules"
                )
            modules = {
                "sqlite": "aiosqlite",
                "postgres": "asyncpg",
                "mysql": "asyncmy",
                "storage": "async_storage_kit",
                "fastapi": "fastapi",
                "migrations": "alembic",
                "docs": "sphinx",
                "test": "pytest",
            }
            if extra in modules:
                code += f"; import {modules[extra]}"
            _run([str(py), "-I", "-c", code], cwd=root)
            print("Isolated install passed:", extra or "core")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    parser.add_argument("--test-sdist", action="store_true")
    parser.add_argument("--test-extras", action="store_true")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    wheels, sdists = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("Exactly one wheel and one source archive are required")
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    wheel_files = _read_wheel(wheels[0])
    sdist_files = _read_sdist(sdists[0])
    _check_metadata(_metadata(wheel_files), project, args.release)
    _check_metadata(_metadata(sdist_files, sdist=True), project, args.release)
    for path in (wheels[0], sdists[0]):
        print(f"SHA256 {path.name}: {hashlib.sha256(path.read_bytes()).hexdigest()}")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for isolated install validation")
    _test_install(wheels[0], uv, args.test_extras)
    if args.test_sdist:
        _test_source(sdist_files, wheel_files, uv)
    print("Distribution validation complete")


if __name__ == "__main__":
    main()
