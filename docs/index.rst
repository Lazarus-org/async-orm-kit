Async ORM Kit documentation
===========================

|pypi-version| |python-versions| |license|

|ci| |pre-commit| |ruff| |codecov| |typed|

|api-docs| |last-commit| |open-issues|

.. |pypi-version| image:: https://img.shields.io/pypi/v/async-orm-kit?style=flat-square&logo=pypi&logoColor=white
   :target: https://pypi.org/project/async-orm-kit/
   :alt: PyPI version

.. |python-versions| image:: https://img.shields.io/pypi/pyversions/async-orm-kit?style=flat-square&logo=python&logoColor=white
   :target: https://pypi.org/project/async-orm-kit/
   :alt: Python versions

.. |license| image:: https://img.shields.io/github/license/Lazarus-org/async-orm-kit?style=flat-square&color=blue
   :target: https://github.com/Lazarus-org/async-orm-kit/blob/main/LICENSE
   :alt: License

.. |ci| image:: https://github.com/Lazarus-org/async-orm-kit/actions/workflows/ci.yml/badge.svg?branch=main
   :target: https://github.com/Lazarus-org/async-orm-kit/actions/workflows/ci.yml
   :alt: CI

.. |pre-commit| image:: https://img.shields.io/badge/pre--commit-enabled-FAB040?style=flat-square&logo=precommit&logoColor=white
   :target: https://github.com/Lazarus-org/async-orm-kit/blob/main/.pre-commit-config.yaml
   :alt: pre-commit

.. |ruff| image:: https://img.shields.io/badge/lint_%26_format-Ruff-D7FF64?style=flat-square&logo=ruff&logoColor=white
   :target: https://docs.astral.sh/ruff/
   :alt: Ruff

.. |codecov| image:: https://codecov.io/gh/lazarus-org/async-orm-kit/branch/main/graph/badge.svg
   :target: https://codecov.io/gh/lazarus-org/async-orm-kit
   :alt: codecov

.. |typed| image:: https://img.shields.io/badge/typing-py.typed-3776AB?style=flat-square
   :target: https://github.com/Lazarus-org/async-orm-kit/blob/main/src/async_orm_kit/py.typed
   :alt: Typed

.. |api-docs| image:: https://img.shields.io/badge/docs-API_reference-0078D4?style=flat-square&logo=readthedocs&logoColor=white
   :target: reference/index.html
   :alt: API documentation

.. |last-commit| image:: https://img.shields.io/github/last-commit/Lazarus-org/async-orm-kit?style=flat-square
   :target: https://github.com/Lazarus-org/async-orm-kit/commits/main/
   :alt: Last commit

.. |open-issues| image:: https://img.shields.io/github/issues/Lazarus-org/async-orm-kit?style=flat-square
   :target: https://github.com/Lazarus-org/async-orm-kit/issues
   :alt: Open issues

**Async ORM Kit** provides an async-first, Django-inspired ``Model``,
``Manager``, and ``QuerySet`` interface built on SQLAlchemy 2.x. It uses the
application's existing ``AsyncSession`` rather than creating its own connection,
transaction manager, or database schema.

This documentation covers version **1.0.0**. See :doc:`compatibility` for
supported environments and :doc:`maintainers/release` for release procedures.

Start here
----------

* :doc:`installation` — requirements, extras and database drivers.
* :doc:`quickstart` — a working async model/query/write example.
* :doc:`guide/sessions` — the most important rule: **you own transactions**.
* :doc:`reference/index` — public API reference, organized by module.

.. toctree::
   :caption: Getting started
   :maxdepth: 2

   introduction
   installation
   quickstart
   compatibility

.. toctree::
   :caption: Core ORM
   :maxdepth: 2

   guide/models
   guide/managers
   guide/querysets
   guide/relationships
   guide/sessions
   guide/validation
   guide/observers

.. toctree::
   :caption: Advanced querying
   :maxdepth: 2

   advanced/expressions
   advanced/aggregation
   advanced/subqueries
   advanced/json-array
   advanced/sets-cte
   advanced/extension
   advanced/pagination
   advanced/locking
   advanced/observability

.. toctree::
   :caption: Optional integrations
   :maxdepth: 2

   integrations-rst/sqlite
   integrations-rst/postgresql
   integrations-rst/mysql
   integrations-rst/storage
   integrations-rst/alembic
   integrations-rst/fastapi

.. toctree::
   :caption: Reference
   :maxdepth: 2

   reference/index
   reference/model
   reference/manager
   reference/queryset
   reference/expressions
   reference/fields
   reference/integrations
   reference/errors

.. toctree::
   :caption: Operations and contributing
   :maxdepth: 2

   guide/security
   guide/performance
   guide/troubleshooting
   maintainers/development
   maintainers/release
   maintainers/architecture

Architecture decisions and design guidance are maintained under ``docs/architecture/``.
