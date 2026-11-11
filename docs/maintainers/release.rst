Release readiness and policy
============================

Release artifacts are built and checked from an immutable source commit.
Publication requires separate maintainer approval.

Release gates
-------------

#. Run native asynchronous SQLite, PostgreSQL and MySQL integration suites
   with **no required tests skipped**, including advanced-query suites.
#. Pass the supported CPython 3.11–3.15 and operating system CI matrix.
#. Run Ruff, mypy, Bandit, dependency audit, and enforce 100% statement and branch coverage.
#. Generate, review and commit an appropriate ``uv.lock`` for reproducible
   development/CI where the workflow requires it.
#. Confirm wheel/sdist packaging, isolation imports and extra dependency
   metadata using the distribution validator.
#. Verify the MIT ``LICENSE`` and PEP 639 license metadata are included in
   both the wheel and source distribution.
#. Confirm actual project URLs, Read the Docs site, release environment
   protection and Trusted Publisher settings.
#. Review every breaking API behavior and update the changelog before
   cutting a stable tag.

The release workflow must operate on the tested immutable commit. Publishing
credentials should be restricted to the publication job and any required
maintainer approval. Do not tag, push or publish automatically from a
local documentation-only edit.
