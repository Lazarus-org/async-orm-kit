"""Validate real execution evidence in pytest JUnit output."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def validate_report(file: Path, *, min_tests: int = 1) -> int:
    if min_tests < 1:
        raise ValueError("min_tests must be positive")
    root = ET.parse(file).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Expected a pytest JUnit testsuite(s) document")
    cases = list(root.iter("testcase"))
    skipped = sum(case.find("skipped") is not None for case in cases)
    failures = sum(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    )
    # Count leaf suites once, even when a producer nests suites.
    suites = [
        suite
        for suite in root.iter("testsuite")
        if not list(suite.iterfind("testsuite"))
    ]
    declared = sum(int(suite.get("tests", "0")) for suite in suites)
    reported_bad = sum(
        int(suite.get(key, "0"))
        for suite in suites
        for key in ("skipped", "failures", "errors")
    )
    tests = len(cases)
    if tests < min_tests or declared != tests or skipped or failures or reported_bad:
        raise ValueError(
            f"Required live tests not fully passed: {tests=} {declared=} {skipped=} {failures=}"
        )
    return tests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--min-tests", type=int, default=1)
    args = parser.parse_args()
    tests = validate_report(args.file, min_tests=args.min_tests)
    print(f"Verified {tests} actual test cases with no skips/failures")


if __name__ == "__main__":
    main()
