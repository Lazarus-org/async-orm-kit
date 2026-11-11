Contributor development workflow
================================

The project's authoritative contribution and conduct policies remain in the
root ``CONTRIBUTING.md`` and ``CODE_OF_CONDUCT.md``. Review
``SECURITY.md`` for responsible vulnerability disclosure.

Developer setup
---------------

.. code-block:: console

   uv lock --check
   uv sync --locked
   uv run pytest -q

The committed ``uv.lock`` pins development and CI dependencies. Review lockfile
changes alongside dependency updates.

Quality tools
-------------

.. code-block:: console

   uv run ruff check src tests .github/scripts
   uv run ruff format --check src tests .github/scripts
   uv run mypy src/async_orm_kit
   uv run bandit -c pyproject.toml -r src/async_orm_kit
   uv run pytest --cov=async_orm_kit --cov-branch --cov-fail-under=0

The offline coverage command measures the portable subset. The complete coverage
gate requires disposable PostgreSQL and MySQL databases as well as SQLite:

.. code-block:: console

   uv sync --locked --extra postgres --extra mysql
   # Set ASYNC_ORM_KIT_POSTGRES_URL and ASYNC_ORM_KIT_MYSQL_URL to disposable
   # databases whose names end in _test.
   uv run pytest --cov=async_orm_kit --cov-branch --cov-fail-under=100

``tox -e coverage`` runs the same complete gate when both database URLs are set.
CI enforces 100% statement and branch coverage over the complete runtime package.
Keep SQL compilation regressions and native provider tests for database-specific
behavior. Do not omit runtime modules or add coverage exclusions to meet the gate.

Build documentation
-------------------

.. code-block:: console

   python -m pip install -e ".[docs]"
   sphinx-build -b html -W --keep-going docs docs/_build/html

The Sphinx source is the ``.rst`` site rooted at ``docs/index.rst``.
Read the Docs builds it using ``docs/conf.py``. Markdown guides and architecture decisions provide supporting material.

Core test philosophy
--------------------

The local SQLite synchronous-session adapter exercises compiled SQL but is
not a native ``AsyncSession`` compatibility check. Keep separate native
``aiosqlite``, ``asyncpg`` and ``asyncmy`` suites and fail CI if required
provider suites are skipped. Database services must be disposable.

Build and distribution
----------------------

.. code-block:: console

   uv run --no-sync uv build
   uv run twine check dist/*

Also run the repository's distribution validation script and inspect wheel
and sdist members, metadata and isolated installations. No release/publish
action is performed by building documentation.
