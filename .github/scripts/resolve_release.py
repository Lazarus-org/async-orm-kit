"""Resolve and validate an immutable stable-release tag without executing tag code."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def resolve(tag: str, *, push_sha: str = "") -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise RuntimeError("Release tags must have the stable form vMAJOR.MINOR.PATCH")
    sha = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise RuntimeError("Could not resolve the release tag to a full commit SHA")
    if push_sha and sha != push_sha:
        raise RuntimeError("Release tag moved or does not match the push event commit")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True
    )
    project = tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"]
    if tag != f"v{project['version']}":
        raise RuntimeError("Release tag disagrees with package project.version")
    version_file = git("show", f"{sha}:src/async_orm_kit/_version.py")
    if not re.search(
        rf'^__version__\s*=\s*["\']{re.escape(project["version"])}["\']$',
        version_file,
        re.M,
    ):
        raise RuntimeError("Release tag disagrees with runtime __version__")
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--push-sha", default="")
    parser.add_argument("--verify-commit", default="")
    args = parser.parse_args()
    sha = resolve(args.tag, push_sha=args.push_sha)
    if args.verify_commit and sha != args.verify_commit:
        raise RuntimeError("Release tag has changed since candidate resolution")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"sha={sha}\ntag={args.tag}\n")
    print(f"Verified release tag {args.tag} at immutable SHA {sha}")


if __name__ == "__main__":
    main()
