Models and mapped columns
=========================

Models subclass :class:`async_orm_kit.Model`, which uses a shared SQLAlchemy
``DeclarativeBase`` registry and metadata. Define columns, relationships,
constraints and indexes using normal SQLAlchemy 2.x declarations.

.. code-block:: python

   from datetime import datetime
   from sqlalchemy import DateTime, ForeignKey, Integer, String, func
   from sqlalchemy.orm import Mapped, mapped_column, relationship
   from async_orm_kit import Model

   class Author(Model):
       __tablename__ = "authors"
       id: Mapped[int] = mapped_column(primary_key=True)
       name: Mapped[str] = mapped_column(String(120), nullable=False)
       posts: Mapped[list["Post"]] = relationship(back_populates="author")

   class Post(Model):
       __tablename__ = "posts"
       id: Mapped[int] = mapped_column(primary_key=True)
       author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
       title: Mapped[str] = mapped_column(String(255), nullable=False)
       author: Mapped[Author] = relationship(back_populates="posts")

The metaclass adds ``objects`` when not explicitly declared and registers
model-specific ``DoesNotExist`` and ``MultipleObjectsReturned`` exceptions.
It does not create database tables. ``Model.metadata`` holds the mapped schema.

Model helpers
-------------

``to_dict()``, ``from_dict()``, ``update_from_dict()``,
``get_field_names()``, ``get_primary_key_names()`` and
``get_primary_key_values()`` provide mapped-column utilities. Be careful
with ``to_dict(follow_relations=True)`` in async applications: loading an
unfetched relationship may require prohibited implicit I/O.

``save(session)``, ``delete(session)`` and ``refresh_from_db(session)`` are
awaitable. They work with the supplied session; they never commit.

.. code-block:: python

   async with SessionFactory() as session:
       async with session.begin():
           author = Author(name="Ada")
           await author.save(session)
           author.name = "Ada Lovelace"
           await author.save(session, update_fields=["name"])

``update_fields`` limits a save to explicitly named fields. For more direct
SQL writes, use :meth:`~async_orm_kit.QuerySet.update` with field expressions.

Composite primary keys
----------------------

Standard SQLAlchemy composite mapped primary keys are supported. QuerySet
``pk`` equality accepts a tuple in declared key order; ``pk__in`` accepts
a sequence of tuples. Correlated ``OuterRef("pk")`` can match tuples. A
**scalar** ``Subquery`` still requires a single projected column.

Model inheritance and custom managers
-------------------------------------

The package rebinds inherited managers per model and allows custom
``Manager(queryset_class=...)``. A manager registered on a model class is
not available as an instance property. For custom policies, see
:doc:`managers` and :doc:`../advanced/extension`.
