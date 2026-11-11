Installation and requirements
=============================

Runtime requirements
--------------------

* CPython **3.11–3.15**, as declared in the package metadata. The full version
  matrix has not yet been verified in hosted CI.
* ``SQLAlchemy[asyncio]>=2.0.40,<3.0``.
* An asynchronous driver suitable for your SQLAlchemy database URL.

Install from a published distribution when one is available, or install from a
checked-out source tree. Version ``1.0.0`` is the stable release target. Do not assume it is currently
available from the Python Package Index.

.. code-block:: console

   # From a local repository checkout
   python -m pip install -e ".[sqlite]"

   # Published package installation, once available
   python -m pip install "async-orm-kit[sqlite]"

Optional extras
---------------

.. list-table:: Extras and their purpose
   :header-rows: 1
   :widths: 20 38 42

   * - Extra
     - Installs
     - Use case
   * - ``sqlite``
     - ``aiosqlite``
     - SQLite via ``sqlite+aiosqlite``
   * - ``postgres``
     - ``asyncpg``
     - PostgreSQL via ``postgresql+asyncpg``
   * - ``mysql``
     - ``asyncmy``
     - MySQL via ``mysql+asyncmy``
   * - ``storage``
     - ``async-storage-kit``
     - Asynchronous storage adapter for ``FileField``
   * - ``migrations``
     - ``alembic``
     - Migration/autogenerate integration
   * - ``fastapi``
     - ``fastapi``
     - Request-scoped session dependency and HTTP error mappings
   * - ``docs``
     - Sphinx and Read the Docs theme
     - Build the RST documentation locally
   * - ``test``
     - Pytest, SQLAlchemy test integrations and other test tools
     - Project development

Core imports do not import FastAPI, Alembic, database drivers or the storage
package. Installing one integration does not start its service or configure it.

Connection URL examples
-----------------------

.. code-block:: python

   SQLITE_URL = "sqlite+aiosqlite:///./app.db"
   POSTGRES_URL = "postgresql+asyncpg://user:password@localhost/app"
   MYSQL_URL = "mysql+asyncmy://user:password@localhost/app?charset=utf8mb4"

Pass URLs to ``sqlalchemy.ext.asyncio.create_async_engine``. Store real
credentials in environment variables or a secret manager rather than checking
them into source control.

Development checkout
--------------------

The repository uses ``uv_build`` and dependency groups. The supplied archive
did not include ``uv.lock``; it must be generated and reviewed in an online
development environment before any ``--locked`` CI operation can be trusted.

.. code-block:: console

   uv lock
   uv sync
   uv run pytest -q

The root ``pyproject.toml`` selects development dependencies by default. To
work with the documentation alone:

.. code-block:: console

   python -m pip install -e ".[docs]"
   sphinx-build -b html -W --keep-going docs docs/_build/html

See :doc:`maintainers/development` for the full quality toolchain.
