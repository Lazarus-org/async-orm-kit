"""Offline contract checks for the authoritative Sphinx RST documentation."""

from __future__ import annotations

import ast
import importlib
import re
import runpy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_sphinx_configuration_and_extras_are_coherent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert any(
        dep.startswith("sphinx>=") for dep in project["optional-dependencies"]["docs"]
    )
    assert any(
        "sphinx-rtd-theme" in dep for dep in project["optional-dependencies"]["docs"]
    )
    rtd = (ROOT / ".readthedocs.yaml").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "sphinx:" in rtd and "configuration: docs/conf.py" in rtd
    assert "sphinx-build -b html" in ci
    config = runpy.run_path(str(DOCS / "conf.py"))
    assert config["project"] == "Async ORM Kit"
    assert config["html_theme"] == "sphinx_rtd_theme"
    assert config["release"] == project["version"]


def test_rst_toc_and_document_links_resolve() -> None:
    rst_files = list(DOCS.rglob("*.rst"))
    assert len(rst_files) >= 35
    root_index = (DOCS / "index.rst").read_text()
    assert ".. toctree::" in root_index
    for file in rst_files:
        text = file.read_text()
        for dest in re.findall(r":doc:`(?:[^`<>\n]*<)?([^`<>\n]+)>?`", text):
            target = dest.rstrip(">")
            assert (file.parent / target).with_suffix(".rst").is_file(), (
                file.relative_to(ROOT),
                target,
            )
    for entry in (
        "installation",
        "quickstart",
        "compatibility",
        "reference/index",
        "advanced/expressions",
        "advanced/sets-cte",
        "integrations-rst/fastapi",
    ):
        assert f"   {entry}\n" in root_index
        assert (DOCS / entry).with_suffix(".rst").is_file()


def test_rst_autodoc_symbols_exist_in_current_source() -> None:
    references = []
    for file in (DOCS / "reference").rglob("*.rst"):
        references.extend(
            re.findall(
                r"^\.\. auto(?:class|function|module):: ([\w.]+)",
                file.read_text(),
                re.M,
            )
        )
    assert len(references) >= 60
    for reference in references:
        module_name, attribute = reference.rsplit(".", 1)
        assert hasattr(importlib.import_module(module_name), attribute), reference


def test_python_examples_are_syntactically_valid() -> None:
    examples = []
    for file in DOCS.rglob("*.rst"):
        lines = file.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip() != ".. code-block:: python":
                continue
            block = []
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or lines[j].startswith("   ")
            ):
                block.append(lines[j][3:] if lines[j].startswith("   ") else "")
                j += 1
            source = "\n".join(block).strip()
            if source:
                examples.append((file, source))
    for code in re.findall(
        r"```python\n(.*?)\n```", (ROOT / "README.md").read_text(), re.S
    ):
        examples.append((ROOT / "README.md", code))
    assert len(examples) >= 60
    for file, source in examples:
        ast.parse(source, filename=str(file))


def test_readme_local_links_resolve() -> None:
    readme = (ROOT / "README.md").read_text()
    for destination in re.findall(r"\]\(([^)]+)\)", readme):
        if "://" in destination or destination.startswith("#"):
            continue
        dest = destination.split("#", 1)[0]
        assert (ROOT / dest).exists(), destination
