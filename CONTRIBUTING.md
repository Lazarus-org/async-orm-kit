# Contributing to async-orm-kit

Contributions to `async-orm-kit` are welcome: bug reports, regression tests, documentation, examples, and focused improvements. Discuss substantial behavior or public API changes with the maintainers before implementation. Follow the [Code of Conduct](CODE_OF_CONDUCT.md) in project discussions and reviews.

## Overview

- [Setting up your environment](#setting-up-your-environment)
- [Testing your changes](#testing-your-changes)
- [Code style and quality checks](#code-style-and-quality-checks)
- [Using pre-commit hooks](#using-pre-commit-hooks)
- [Commit messages](#commit-messages)
- [Creating a pull request](#creating-a-pull-request)
- [Reporting issues](#reporting-issues)
- [Packaging and release maintenance](#packaging-and-release-maintenance)
- [Resources](#resources)

## Setting up your environment

### Fork and clone

After the standalone repository is confirmed, fork [Lazarus-org/async-orm-kit](https://github.com/Lazarus-org/async-orm-kit), then clone your fork. Replace `YOUR_USERNAME` with your GitHub username:

```bash
git clone https://github.com/YOUR_USERNAME/async-orm-kit.git
cd async-orm-kit
git remote add upstream https://github.com/Lazarus-org/async-orm-kit.git
git switch -c feature/describe-your-change
```

Use a focused branch for a feature, fix, or documentation improvement. Keep credentials, local environments, IDE metadata, and generated build outputs out of commits.

### Install Python and uv

Development targets **CPython 3.11, 3.12, 3.13, 3.14, and 3.15**. Other Python implementations are not currently supported. Install [uv](https://docs.astral.sh/uv/getting-started/installation/); its minimum version is declared in `pyproject.toml`.

From the repository root, install the locked development environment:

```bash
uv sync --locked
```

After generating `uv.lock` with `uv lock`, plain `uv sync` also installs the default `dev` group. `--locked` additionally requires `uv.lock` to match the project metadata. Generate and commit `uv.lock` first: it was missing from the uploaded source archive. uv creates `.venv` and installs the project in editable mode. Use `uv run` on both POSIX shells and PowerShell; activating the environment is unnecessary.

The development group includes test dependencies, optional database and FastAPI integrations, and quality tools. The `test` extra and `quality` dependency group remain available for tox and distribution checks. The published core has **one mandatory runtime dependency: SQLAlchemy 2.x**. For a runtime-only checkout environment:

```bash
uv sync --locked --no-dev
```

Run `uv sync --locked` again before development checks.

### Update dependencies deliberately

Commit dependency changes in `pyproject.toml` together with the resulting `uv.lock`. Update one locked dependency with:

```bash
uv lock --upgrade-package PACKAGE
uv sync --locked
uv pip check
```

Replace `PACKAGE` with the dependency being updated, then verify compatibility and quality checks. Ruff is constrained to the **0.17.x** release line; update its dependency pins, required version, and tox configuration together when intentionally changing that version.

## Testing your changes

### Full suite and coverage

Run the complete suite before opening a pull request:

```bash
uv run --locked pytest --cov=async_orm_kit
```

Coverage includes **statements and branches**, including Python subprocesses used by import and concurrency regressions. The long-term target is **100%**, but the current development CI records coverage without asserting this target until missing branches are tested. Do not label that target achieved until all executed checks prove it. Do not exclude executable package behavior just to improve the reported percentage.

Add tests demonstrating the behavior changed by a fix or feature. Existing tests cover QuerySets, SQL expressions, joins, database-neutral writes, FastAPI/Alembic integration, and the FileField adapter; the live-driver suites still need hosted evidence.

### Focused checks

Select a test file or group without requesting a full-package coverage report:

```bash
uv run --locked pytest tests/test_queryset_expressions.py
uv run --locked pytest tests/integration
uv run --locked pytest tests/test_foundation.py
```

Use synchronization events or barriers and bounded subprocess timeouts for concurrency tests. Check that failures notify waiting readers and permit recovery. Avoid timing assumptions that make tests unreliable on slower runners.

### Compatibility checks

tox uses `tox-uv` to create environments and install packages with uv:

```bash
uv run --locked tox list
uv run --locked tox -e py311,py312,py313,py314,py315
uv run --locked tox -e lint,type
```

Each Python environment installs a wheel and runs the full suite with coverage. Missing interpreters cause failures rather than silently skipping a compatibility claim. Report the actual interpreter and dependency versions tested. Local results do not establish that the hosted GitHub Actions matrix passed.

## Code style and quality checks

Ruff owns Python formatting, import ordering, and the configured lint rules. Run checks from the repository root:

```bash
uv run --locked ruff check src tests .github/scripts
uv run --locked ruff format --check src tests .github/scripts
uv run --locked mypy src/
uv run --locked bandit -c pyproject.toml -r src/async_orm_kit
uv run --locked python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"
uv lock --check --offline
uv pip check
```

To apply Python formatting and supported lint fixes:

```bash
uv run --locked ruff check --fix src tests .github/scripts
uv run --locked ruff format src tests .github/scripts
```

Pre-commit validates `pyproject.toml`, YAML and whitespace; it does not silently rewrite contributor policies. Review automatic fixes before committing them. Verify Python classifiers and metadata whenever constraints change.

Preserve the package's design:

- Keep mandatory runtime dependencies limited to SQLAlchemy and avoid importing optional drivers or integrations at package import time.
- Preserve lazy QuerySet evaluation, deterministic query plans, correct relationship paths, and explicit transaction ownership.
- Keep application session and transaction ownership outside the ORM; do not commit or roll back caller-owned transactions.
- Preserve SQLAlchemy identity-map behavior, QuerySet cloning, and isolation of mutable query state.
- Delegate engine configuration and external request validation to the application or its framework.
- Preserve exception causes without exposing configuration values in package-generated messages.
- Document driver-specific behavior, async session constraints, and platform-dependent filesystem/storage guarantees.

## Using pre-commit hooks

The development environment already includes pre-commit. Install both configured Git hook stages:

```bash
uv run --locked pre-commit install --hook-type pre-commit --hook-type commit-msg
```

The configuration provides `pre-commit` and `commit-msg` hooks; install both stages as described below. File checks cover TOML/YAML, whitespace, file endings, file size, case conflicts, merge conflicts, and docstring placement. Local hooks validate configuration and run Ruff, mypy, and Bandit; the full offline test hook is available at the manual stage and records branch coverage. The commit-message stage validates Conventional Commits.

Run the same checks used by CI on all tracked files:

```bash
uv run --locked pre-commit run --all-files
```

Run just the full test hook explicitly:

```bash
uv run --locked pre-commit run pytest --hook-stage manual --all-files
```

If a hook changes a file, review the change, stage it, and retry the commit. When changing `.pre-commit-config.yaml`, stage that configuration before committing so pre-commit can use it. Address failures before submitting a pull request.

## Commit messages

Stage the intended files and use Commitizen:

```bash
uv run --locked cz commit
```

Use an imperative summary describing the resulting behavior. Add a body when it helps explain the problem, implementation, or validation. Reference real issues in the footer: `Closes #123` for a resolved issue or `Refs #123` for ongoing work.

```text
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Examples appropriate to this package:

| Change | Example subject |
| --- | --- |
| Feature | `feat(settings): support an additional settings source` |
| Bug fix | `fix(settings): release waiting readers after initialization fails` |
| Tests | `test(loader): cover inherited and unset slots` |
| Documentation | `docs(integrations): explain AsyncSession lifecycle` |
| Refactoring | `refactor(loader): simplify public attribute discovery` |
| Performance | `perf(settings): reduce work on cached reads` |
| Build tooling | `build(packaging): validate source archive contents` |
| CI | `ci: verify published action references` |
| Dependencies | `chore(deps): update locked development tools` |

A complete example:

```text
fix(settings): notify waiting readers after initialization fails

Allow another reader to retry the selected source after a failed attempt.
Add a bounded regression test for failure notification and recovery.

Closes #123
```

Commitizen uses its `uv` version provider. The package version is stored in `project.version`; an intentional release bump also updates the matching package entry in `uv.lock`. `cz commit` does not bump the version or create a tag.

## Creating a pull request

1. Keep the pull request focused and include meaningful tests for changed behavior.
2. Run relevant checks, then the full suite and pre-commit hooks.
3. Review the diff and commit only intended files with Commitizen.
4. Push the working branch to your fork:

   ```bash
   git push origin feature/describe-your-change
   ```

5. Open a pull request against `main` in [Lazarus-org/async-orm-kit](https://github.com/Lazarus-org/async-orm-kit/pulls).
6. Explain the concrete problem, resulting behavior, related issues, and validation. Identify public API or compatibility implications.
7. Respond to review feedback and rerun checks affected by revisions.

Do not include credentials, production configuration values, personal data, or unrelated generated files in examples, fixtures, logs, or pull requests. Check the actual hosted workflow results as well as local validation.

## Reporting issues

Use the [issue tracker](https://github.com/Lazarus-org/async-orm-kit/issues) for bugs and feature requests. Search for an existing report first. For a bug, include:

- Package version, Python implementation/version, operating system, and relevant optional dependency versions.
- A minimal reproducible example with sensitive values removed.
- Expected and actual behavior, including a sanitized traceback when helpful.
- Whether the issue concerns the core, an example, a framework interaction, or packaging.

For feature requests, describe the use case and how it fits the package's SQLAlchemy query-layer responsibilities. Report undisclosed vulnerabilities privately through [SECURITY.md](SECURITY.md), and conduct concerns through [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Packaging and release maintenance

Builds use `uv_build`. `pyproject.toml` defines the source-archive inclusion rules. Wheels contain the runtime package, typing marker, and metadata; LICENSE must be approved before publication. Source archives also include tests, examples, maintainer documents, the lockfile, and CI tooling.

Build into an empty output directory and validate the exact artifacts:

```bash
uv build
uv run --locked python -m twine check --strict dist/*
uv run --locked python .github/scripts/validate_distribution.py dist --test-sdist
```

Alternatively, use an isolated tox build environment:

```bash
uv run --locked tox -e build
```

The validator checks metadata and archive contents, installs the wheel with SQLAlchemy but no optional integrations, executes core smoke tests and source tests, and compares rebuilt wheel contents.

Version changes, release changelog generation, tags, and publication are maintainer release tasks. Routine contributions should retain the current version. Maintainers should verify that project metadata, the lockfile, changelog, and any proposed tag agree, and require actual successful CPython matrix results before release. The release workflow validates and retains build artifacts, then publishes them to PyPI when `PYPI_PUBLISH_ENABLED` is `true` and the `pypi` environment permits deployment.

Inspect the version without changing it:

```bash
uv run --locked cz version --project
```

### Configure automatic PyPI publishing

The workflow follows a build/test/publish split. The build and compatibility jobs have read-only permissions. Only the separate publish job receives `id-token: write`, runs in the `pypi` environment, and uploads the validated `release-distributions` artifact with `uv publish --trusted-publishing always`. No long-lived PyPI token is needed for this path.

Configure these once for this repository:

1. Create the GitHub environment **`pypi`**. Configure its reviewers and deployment rules as needed. Allow version-tag deployments and, if using manual recovery from `main`, that branch as well.
2. Register a GitHub Trusted Publisher on PyPI with these exact values:

   | Field | Value |
   | --- | --- |
   | PyPI project | `async-orm-kit` |
   | Repository owner | `Lazarus-org` |
   | Repository name | `async-orm-kit` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

   For the first upload, use a [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/). For an existing project you manage, add it in the project's [publishing settings](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
3. Set the GitHub repository Actions variable **`PYPI_PUBLISH_ENABLED`** to **`true`**. The build job writes a visible notice and summary when publishing is disabled; passing validation alone does not mean an upload occurred.

After configuration, a `v*.*.*` tag push validates and publishes the release. The publish job waits for all five CPython jobs, the quality job, and artifact validation. Environment protection rules apply before the publish job can run.

### Publish an existing tag after updating the workflow

A tag uses the workflow definition committed with it. Rerunning an older validation-only tag workflow does not add a publishing job.

To publish the already pushed `v1.0.0` after merging this workflow update, open **Actions → Release → Run workflow**, select **`main`**, and set **`release_tag`** to **`v1.0.0`**. The updated workflow checks out that exact tag for both CI and building, verifies its package version, and publishes only after validation succeeds. Do not move or recreate the tag.

The automated workflow builds and publishes its own validated artifacts. The detailed manual/TestPyPI-first procedure below is an alternative for maintainers publishing local artifacts; keep automatic publishing disabled during that manual procedure to avoid an earlier production upload.

### 1. Prepare the release branch

Start from an up-to-date `main` with a clean working tree and fetch existing tags:

```bash
git switch main
git pull --ff-only origin main
git fetch origin --tags
git switch -c release/prepare-next-release
uv sync --locked
```

Confirm the intended release version, project ownership on PyPI, and release notes with the maintainers. The examples below use the initial **1.0.0** release; substitute the selected version in tag names, installation checks, and release notes for later releases.

### 2. Update the version and changelog

For the first 1.0.0 release, change `project.version` from `1.0.0.dev8` to `1.0.0` after all gates pass and finalize the existing `CHANGELOG.md` entry: replace its `Unreleased` label with the release date and review the listed changes. An initial release does not require an increment to 1.0.1.

For subsequent releases, preview the version increment and changelog inferred from Conventional Commits:

```bash
uv run --locked cz bump --dry-run --changelog
```

After reviewing the proposed version, update the version files and changelog without automatically committing or tagging:

```bash
uv run --locked cz bump --version-files-only --changelog
uv lock --check --offline
uv run --locked cz version --project
```

Commitizen uses configured version files; the maintainer must regenerate `uv.lock` and review the package version entry. Review the generated changelog for accurate descriptions and duplicate or outdated unreleased entries. Follow [Commitizen's bump documentation](https://commitizen-tools.github.io/commitizen/commands/bump/) for explicit increments or prerelease versions when needed.

Changelog generation is included in the bump command above. If generating release notes separately, preview the selected tag range first:

```bash
uv run --locked cz changelog --dry-run
```

Review the range and output before using `cz changelog --incremental` to update `CHANGELOG.md`; it updates the changelog relative to existing entries and tags. It does not mean that every earlier release should be regenerated. See the [changelog command documentation](https://commitizen-tools.github.io/commitizen/commands/changelog/).

### 3. Validate and commit the release preparation

Run the full suite, hooks, and packaging checks described above. Review the version, lockfile, changelog, metadata, and README together. Stage the release files and use Commitizen:

```bash
git add pyproject.toml uv.lock CHANGELOG.md
uv run --locked cz commit
```

A suitable subject is `chore(release): prepare version 1.0.0`. Include any related issue references and describe the validation. Submit and merge the release-preparation pull request through the normal review process. Require successful hosted checks for all five CPython versions and the quality job on the release commit.

### 4. Create and push the release tag

After the release-preparation changes are merged and their hosted checks pass, update the local release commit and create an annotated tag:

```bash
git switch main
git pull --ff-only origin main
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0
```

The configured tag format is `v$version`. Verify that the tag identifies the intended commit and matches `project.version`; do not move an existing published release tag. Pushing a version tag starts the release workflow, which reruns compatibility and quality checks, validates the tag, and uploads distributions. When automatic publishing is enabled, its separate publish job downloads those same artifacts and uploads them to PyPI using Trusted Publishing. Wait for the validation and publish jobs to succeed. TestPyPI uploads remain a manual option.

### 5. Build the exact tagged artifacts

Build from the release tag into an empty `dist/release` directory. Move aside older artifacts before starting so they cannot be included in an upload. If using artifacts downloaded from the successful release workflow instead, retain and validate those exact files rather than mixing them with a new build.

```bash
git switch --detach v1.0.0
uv sync --locked
uv build --no-sources --out-dir dist/release
uv run --locked python -m twine check --strict dist/release/*
uv run --locked python .github/scripts/validate_distribution.py dist/release --test-sdist
```

Keep the wheel, source archive, and SHA-256 hashes printed by the validator together for release review. Upload only the reviewed files. Rebuilding creates a new candidate that must be validated again.

### 6. Publish to TestPyPI and verify installation

TestPyPI is a separate package index with separate accounts and tokens. Set `UV_PUBLISH_TOKEN` in the publishing shell or secret store to the appropriate TestPyPI token, then upload the validated artifacts:

```bash
uv publish --publish-url https://test.pypi.org/legacy/ dist/release/*
```

Test installation from that index in an isolated environment, independently of the local checkout:

```bash
uv run --isolated --no-project --default-index https://test.pypi.org/simple/ --refresh-package async-orm-kit --with "async-orm-kit==1.0.0" python -c "from importlib.metadata import version; from async_orm_kit import Model; assert version('async-orm-kit') == '1.0.0'; assert Model.metadata is not None"
```

The core has one mandatory runtime dependency (SQLAlchemy), so this check does not require an additional package index for optional database drivers. Review the rendered project description and metadata on TestPyPI as well.

### 7. Publish to PyPI and verify the release

Replace the publishing credentials with the appropriate **PyPI** token before uploading to production. TestPyPI credentials do not authenticate to PyPI. Publish the same validated wheel and source archive:

```bash
uv publish --publish-url https://upload.pypi.org/legacy/ dist/release/*
```

Verify that the released version can be installed from PyPI in an isolated environment:

```bash
uv run --isolated --no-project --default-index https://pypi.org/simple/ --refresh-package async-orm-kit --with "async-orm-kit==1.0.0" python -c "from importlib.metadata import version; from async_orm_kit import Model; assert version('async-orm-kit') == '1.0.0'; assert Model.metadata is not None"
```

Confirm the version, description, license, Python support, project URLs, and both distribution files on the package index. Create the corresponding GitHub release with the reviewed changelog and, when attaching artifacts, the same validated files.

Keep tokens out of source files, commits, logs, and command-line arguments. `UV_PUBLISH_TOKEN` is supported for manual uv uploads. The automated workflow uses PyPI Trusted Publishing and requires the one-time configuration described below. See [uv's publishing guide](https://docs.astral.sh/uv/guides/package/) for authentication and upload options.

### 8. Handle an interrupted upload

If an upload partially succeeds, retain the exact artifacts and review which files reached the index. Retry with the same files and use uv's existing-file checks as appropriate. Do not replace an already published file with a different build under the same version. If the released package needs a code or metadata correction, prepare a new version and repeat validation.

## Resources

- [Project README](README.md) and [integration guides](docs/integrations/async-session.md)
- [uv documentation](https://docs.astral.sh/uv/)
- [Ruff documentation](https://docs.astral.sh/ruff/)
- [pytest documentation](https://docs.pytest.org/en/stable/)
- [Coverage.py documentation](https://coverage.readthedocs.io/)
- [Mypy documentation](https://mypy.readthedocs.io/)
- [Bandit documentation](https://bandit.readthedocs.io/)
- [Pre-commit documentation](https://pre-commit.com/)
- [Commitizen documentation](https://commitizen-tools.github.io/commitizen/)
- [Authors](AUTHORS.md), [contributors](CONTRIBUTORS.md), [security policy](SECURITY.md), and [Code of Conduct](CODE_OF_CONDUCT.md)
