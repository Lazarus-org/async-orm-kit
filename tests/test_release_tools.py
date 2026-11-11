"""Regression tests for fail-closed release validation tools."""

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = runpy.run_path(str(ROOT / ".github/scripts/check_pytest_junit.py"))


@pytest.mark.parametrize(
    "body",
    [
        '<testsuite tests="16"/>',
        '<testsuite tests="1" skipped="1"><testcase><skipped/></testcase></testsuite>',
        '<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite>',
        '<testsuite tests="1" errors="1"><testcase><error/></testcase></testsuite>',
        '<testsuite tests="2"><testcase/></testsuite>',
        "<testsuites/>",
        "<not-junit/>",
    ],
)
def test_required_junit_rejects_missing_or_failed_evidence(tmp_path, body):
    report = tmp_path / "results.xml"
    report.write_text(body)
    with pytest.raises(ValueError):
        TOOLS["validate_report"](report)


def test_required_junit_accepts_nested_passing_suites(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuites><testsuite tests="2"><testsuite tests="2"><testcase name="one"/><testcase name="two"/></testsuite></testsuite></testsuites>'
    )
    assert TOOLS["validate_report"](report, min_tests=2) == 2


def test_distribution_inventory_excludes_generated_documentation():
    validator = runpy.run_path(str(ROOT / ".github/scripts/validate_distribution.py"))
    assert not any(
        "_build" in Path(name).parts for name in validator["_source_required"]()
    )
    for name in (
        "docs/_build/html/index.html",
        ".validation/private",
        ".release-venv/python.exe",
    ):
        with pytest.raises(RuntimeError, match="Unsafe archive"):
            validator["_safe_path"](name)
