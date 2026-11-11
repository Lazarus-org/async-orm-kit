# Code of Conduct for Contributing to async-orm-kit

We welcome contributions to `async-orm-kit`. Please help maintain a respectful, constructive environment for everyone involved. This document retains the contribution workflow, release guidance, and commit-message examples alongside the community expectations below. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development and publishing guide.

## Community Expectations

- Treat people with respect regardless of experience, background, identity, or ability.
- Give specific feedback about the work, explain technical disagreements, and accept constructive review.
- Respect privacy, personal boundaries, authorship, and other people's time.
- Avoid harassment, discrimination, threats, personal attacks, sexualized content, and deliberate disruption.
- Do not share credentials, private configuration, personal information, or private correspondence without permission.
- Do not retaliate against someone for reporting a concern or participating in its review.

These expectations apply in project-managed issues, pull requests, reviews, discussions, and other community spaces, and when representing the project publicly.

Report conduct concerns privately to [Aryan Niknezhad](mailto:aryan513966@gmail.com) or [Mehrshad Mirshekary](mailto:mehrshad_mirshekary@email.com). If a report involves one maintainer, contact the other. Include relevant context and links, and keep private evidence out of public issues. Maintainers should handle reports discreetly and avoid deciding complaints about themselves.

Maintainers may request a change in behavior, warn participants, remove harmful content, or temporarily or permanently restrict participation, depending on severity and repeated behavior. Affected participants may privately request clarification or reconsideration from a maintainer not involved in the complaint. Report software vulnerabilities through [SECURITY.md](SECURITY.md).

## Contribution Workflow

### 1. Fork and Clone

After repository confirmation, fork [Lazarus-org/async-orm-kit](https://github.com/Lazarus-org/async-orm-kit), then clone your fork. Replace `YOUR_USERNAME` with your GitHub username:

```bash
git clone https://github.com/YOUR_USERNAME/async-orm-kit.git
cd async-orm-kit
git remote add upstream https://github.com/Lazarus-org/async-orm-kit.git
```

### 2. Create a Branch

Create a focused branch for the feature, fix, or documentation update:

```bash
git switch -c feature/your-feature-name
```

### 3. Install Dependencies

Use CPython 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/). Supported interpreters are CPython 3.11–3.15. Install the locked development environment and Git hooks:

```bash
uv sync --locked
uv run --locked pre-commit install
```

The default `dev` group includes tests and quality tools after the maintainer generates the missing `uv.lock`. The core package requires SQLAlchemy 2.x, but FastAPI, database drivers, Alembic, and file storage remain optional. `uv run` works on both PowerShell and POSIX shells without environment activation.

### 4. Write Code and Tests

Add meaningful tests for changed behavior and run the complete suite:

```bash
uv run --locked pytest --cov=async_orm_kit
```

The long-term coverage objective remains **100% statement and branch coverage**. Current measured package coverage is lower and must be reported rather than described as passing a 100% release gate. Include async transaction, relationship-planning, and integration error paths; platform-specific and live-provider skips must be disclosed.

For focused feedback without a full-package coverage report:

```bash
uv run --locked pytest tests/test_queryset_expressions.py
```

### 5. Run Code Quality Checks

Ruff **0.17.3** owns Python formatting and import checks. Run the configured checks:

```bash
uv run --locked ruff check src tests .github/scripts
uv run --locked ruff format --check src tests .github/scripts
uv run --locked mypy src/
uv run --locked bandit -c pyproject.toml -r src/async_orm_kit
uv run --locked pre-commit run --all-files
```

Pre-commit also checks file hygiene, formats TOML/tox configuration, verifies the lockfile offline, and runs the full test suite. Review and stage automatic fixes before retrying a commit. The compatibility matrix can be exercised with:

```bash
uv run --locked tox -e py311,py312,py313,py314,py315
```

Missing interpreters cause failures. Report actual local versions tested and inspect hosted CI results before claiming matrix compatibility.

### 6. Commit Your Changes

Stage the intended files and use Commitizen with the Conventional Commits format:

```bash
uv run --locked cz commit
```

Use real issue references in the footer: `Closes #123` for a resolved issue or `Refs #123` for ongoing work. Stage changes to `.pre-commit-config.yaml` before committing so the hooks can use that configuration.

### 7. Push and Create a Pull Request

Push the working branch to your fork:

```bash
git push origin feature/your-feature-name
```

Open a pull request against `main` in the [upstream repository](https://github.com/Lazarus-org/async-orm-kit/pulls). Explain the problem, resulting behavior, relevant issues, and validation. Respond constructively to review and rerun checks affected by revisions.

### 8. Bump the Version for a Maintainer Release

Version changes and the following release steps are maintainer tasks. Routine contributions retain the current package version.

The version is stored under `[project]` and the package `_version.py`; Commitizen tracks both:

```toml
[project]
name = "async-orm-kit"
version = "1.0.0.dev8"

[tool.commitizen]
name = "cz_conventional_commits"
version = "1.0.0.dev8"
tag_format = "v$version"
```

For the initial 1.0.0 release, promote `1.0.0.dev8` to stable `1.0.0` only after release gates pass and finalize the changelog entry. For later releases, preview the proposed increment, then update the version files and changelog:

```bash
uv run --locked cz bump --dry-run --changelog
uv run --locked cz bump --version-files-only --changelog
uv lock --check --offline
```

Commitizen updates the configured version files; regenerate and verify `uv.lock` separately. `--version-files-only` leaves the commit and tag for the reviewed release steps. Review the proposed version and generated notes before proceeding.

### 9. Generate and Review the Changelog

The bump command above already generates the changelog. When generating notes separately, preview them first:

```bash
uv run --locked cz changelog --dry-run
```

After reviewing the selected range and existing entries, update the changelog if needed:

```bash
uv run --locked cz changelog --incremental
```

Incremental generation uses existing changelog entries and tags; it is not a guarantee that only one new tag will be processed. Review release dates, duplicate entries, and technical accuracy. Include the reviewed changelog with the release preparation:

```bash
git add pyproject.toml uv.lock CHANGELOG.md
uv run --locked cz commit
```

Submit and merge the release-preparation changes through normal review. See the [release-maintenance guide](CONTRIBUTING.md#packaging-and-release-maintenance) for the full sequence.

### 10. Push the Release Code and Tag

After the release-preparation pull request is merged and hosted compatibility and quality checks pass, update the local release commit and tag it. These commands illustrate the initial 1.0.0 release; substitute the selected version for later releases:

```bash
git switch main
git pull --ff-only origin main
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0
```

The tag must match `project.version` and identify the intended commit. The release workflow validates the tagged package and, when `PYPI_PUBLISH_ENABLED=true`, publishes its validated artifacts through PyPI Trusted Publishing in the `pypi` environment. Wait for both validation and publishing to succeed. The [automatic publishing setup](CONTRIBUTING.md#configure-automatic-pypi-publishing) lists the one-time GitHub and PyPI configuration. Do not move an existing published release tag.

### 11. Export Dependencies When Needed

`uv.lock` is the normal development dependency record. If another tool needs requirements files, export them with uv:

```bash
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv export --locked --only-dev --no-emit-project --format requirements-txt --output-file requirements-dev.txt
```

The runtime export has SQLAlchemy as the sole mandatory third-party runtime requirement because the core has SQLAlchemy as the single mandatory dependency. The development export includes test and quality tools. Keep hashes in exported files and regenerate them from the lockfile when needed; exported requirements do not replace `uv.lock` as the project's source of dependency resolution.

### 12. Build and Validate the Tagged Artifacts

Build from the selected release tag into an empty output directory:

```bash
git switch --detach v1.0.0
uv sync --locked
uv build --no-sources --out-dir dist/release
uv run --locked python -m twine check --strict dist/release/*
uv run --locked python .github/scripts/validate_distribution.py dist/release --test-sdist
```

The validator checks wheel/source inventory and metadata, tests extracted sources, compares rebuilt wheel contents, and verifies isolated core operation with SQLAlchemy but no optional integrations. Retain the exact artifacts and printed SHA-256 hashes for release review. If using artifacts downloaded from hosted CI, validate and retain those exact files.

For an already pushed tag, use the [existing-tag workflow instructions](CONTRIBUTING.md#publish-an-existing-tag-after-updating-the-workflow); moving the tag is unnecessary. The following steps describe manual publication of the local artifacts.

### 13. Publish to TestPyPI

Set `UV_PUBLISH_TOKEN` in the publishing shell or secret store to the appropriate TestPyPI token, then upload the reviewed artifacts:

```bash
uv publish --publish-url https://test.pypi.org/legacy/ dist/release/*
```

Check the rendered metadata and test installation independently of the checkout:

```bash
uv run --isolated --no-project --default-index https://test.pypi.org/simple/ --refresh-package async-orm-kit --with "async-orm-kit==1.0.0" python -c "from importlib.metadata import version; from async_orm_kit import Model; assert version('async-orm-kit') == '1.0.0'; assert Model.metadata is not None"
```

### 14. Publish to PyPI and Complete the Release

Use the appropriate PyPI credentials and upload the same validated wheel and source archive:

```bash
uv publish --publish-url https://upload.pypi.org/legacy/ dist/release/*
```

TestPyPI and PyPI use separate accounts and tokens. Keep tokens out of source files, commits, logs, and command-line arguments. The automated release job uses Trusted Publishing without a long-lived token. Configure its publisher and enable variable as described in [CONTRIBUTING.md](CONTRIBUTING.md#configure-automatic-pypi-publishing). For this manual/TestPyPI-first procedure, keep automatic publishing disabled until you choose the production publishing path.

Verify installation from PyPI:

```bash
uv run --isolated --no-project --default-index https://pypi.org/simple/ --refresh-package async-orm-kit --with "async-orm-kit==1.0.0" python -c "from importlib.metadata import version; from async_orm_kit import Model; assert version('async-orm-kit') == '1.0.0'; assert Model.metadata is not None"
```

Confirm both distributions and the metadata on the index, then create the corresponding GitHub release with the reviewed notes and artifacts. If an upload partially succeeds, retain the same artifacts for a retry. A correction to an already published build requires a new version. Further details are in the [publishing guide](CONTRIBUTING.md#packaging-and-release-maintenance).

## Commitizen Message Rule

Commitizen follows Conventional Commits. Structure messages as follows:

```text
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Use an imperative subject and explain the resulting behavior. The examples below illustrate message structure; use descriptions and issue references matching the actual change.

## Examples of Commit Messages

### 1. Initialization of the Core

```text
feat(core): initialize the async SQLAlchemy query layer

- Add explicit session ownership and manager bindings
- Build QuerySets lazily from SQLAlchemy models
- Export dedicated database exceptions
```

### 2. Prepare a Release Build

```text
chore(release): prepare version 1.0.0

- Finalize the changelog entry
- Validate wheel and source archive contents
- Record compatibility and quality results
```

Creating a release commit does not itself create a Git tag or publish a package.

### 3. Add a Feature

```text
feat(query): add immutable QuerySet composition

- Resolve validated expression and lookup paths
- Preserve independent query state across clones
- Document query compilation guarantees
```

### 4. Fix a Bug

```text
fix(session): preserve caller transaction on write failure

- Avoid hidden commit or rollback inside QuerySets
- Preserve original SQLAlchemy exception context
- Add transaction-boundary regression tests
```

### 5. Update Documentation

```text
docs(integration): explain FastAPI session ownership

- Describe request-scoped AsyncSession lifetime
- Clarify transaction boundaries and rollback rules
- Update the executable application instructions
```

### 6. Update Dependencies

```text
chore(deps): update locked development tools

- Update the relevant pyproject dependency constraints
- Regenerate uv.lock
- Verify the supported interpreters and quality checks
```

### 7. Update the Version for a Future Release

```text
chore(release): prepare the next patch release

- Update project.version and its uv.lock entry
- Review the generated changelog
- Validate the candidate artifacts before tagging
```

### 8. Add Unit Tests

```text
test(query): cover nested relationship lookups

- Exercise nullable reverse relationship paths
- Verify query-planner join promotion and clone isolation
- Expand statement and branch coverage without exclusions
```

### 9. Refactor the Codebase

```text
refactor(planner): simplify relationship path resolution

- Keep supported query semantics unchanged
- Preserve lazy QuerySet evaluation
- Retain regression coverage for invalid paths
```

### 10. Improve Performance

```text
perf(query): reduce repeated relationship compilation

- Reduce redundant joins for repeated filter paths
- Preserve eager-loading semantics
- Verify existing SQL behavior and measure the improvement
```

Thank you for contributing to `async-orm-kit`. Follow these guidelines, keep discussions constructive, and refer to [CONTRIBUTING.md](CONTRIBUTING.md) for the complete development and release reference.
