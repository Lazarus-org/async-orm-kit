Compatibility and validation status
===================================

The package supports CPython 3.11 through 3.15 and SQLAlchemy 2.x.
Hosted CI validates all 15 combinations of these Python versions with Ubuntu,
Windows, and macOS. The complete validation also covers native PostgreSQL and
MySQL, strict documentation builds, quality checks, distributions, and the
100% statement and branch coverage gate.

See the `successful hosted validation run
<https://github.com/Lazarus-org/async-orm-kit/actions/runs/43039484713>`_
for the recorded evidence. Every release must pass CI again on its own commit.

Native database evidence
-------------------------

* SQLite: real ``aiosqlite`` / AsyncSession suites execute without native-test skips.
* PostgreSQL 16.15 / asyncpg 0.32.0: base, advanced, and shared acceptance
  contracts passed in a disposable PostgreSQL 16 container.
* MySQL 8.4.13 / asyncmy 0.2.18: base, advanced, and shared acceptance
  contracts passed in a disposable MySQL 8.4 container. The MySQL extra
  includes cryptography for the server's default authentication.
* FastAPI, Alembic, and local async-storage-kit integrations execute locally.
  External object-storage providers and production deployments were not tested.

Representative contracts do not establish every possible server configuration,
query combination, or driver version. Run the test suite and the release checks in :doc:`maintainers/release`
to validate a deployment environment.

Portability considerations
---------------------------

* ``JSONContains``, ``JSONHasKey`` and ``Array*`` require PostgreSQL JSONB/ARRAY
  mappings. Unsupported dialects raise compilation errors.
* MySQL conflict writes report affected rows according to server semantics.
  MySQL ``update(returning=True)`` and conflict-insert returning are unsupported.
* Row locking requires an explicit transaction on a supported backend; SQLite
  row locks are rejected. See :doc:`advanced/locking`.
* Grouping full PostgreSQL models with JSON columns is subject to PostgreSQL's
  JSON equality limitations; select grouping fields explicitly or use JSONB.
* Manager ``use_cache`` / ``cache_ttl`` remain reserved. Result processors are
  an unimplemented legacy placeholder and raise ``NotImplementedError``.
* The package ships ``py.typed``. Runtime checks, Ruff, and mypy do not imply
  that every dynamic result projection has a specialized static return type.

Release approval
----------------

The source and distributions use the MIT license. The canonical repository is
`Lazarus-org/async-orm-kit <https://github.com/Lazarus-org/async-orm-kit>`_.
Stable publication requires successful hosted Linux/macOS CI evidence. The
package version is ``1.0.0``. See :doc:`maintainers/release`.
