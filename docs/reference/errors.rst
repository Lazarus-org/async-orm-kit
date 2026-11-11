Exceptions and error handling
=============================

Query-construction and execution exceptions share the
:class:`~async_orm_kit.QuerySetError` base hierarchy. Actual SQLAlchemy
errors may also escape depending on the execution path.

.. autoclass:: async_orm_kit.QuerySetError

.. autoclass:: async_orm_kit.ModelAttributeError

.. autoclass:: async_orm_kit.RelationshipError

.. autoclass:: async_orm_kit.FilterError

.. autoclass:: async_orm_kit.InvalidLookupError

.. autoclass:: async_orm_kit.InvalidRelationshipValueError

.. autoclass:: async_orm_kit.QueryStateError

.. autoclass:: async_orm_kit.ExpressionTypeError

.. autoclass:: async_orm_kit.ObjectDoesNotExist

.. autoclass:: async_orm_kit.MultipleObjectsReturned

.. autoclass:: async_orm_kit.DatabaseError

.. autoclass:: async_orm_kit.FieldValidationError

Some models define model-specific ``DoesNotExist`` and
``MultipleObjectsReturned`` subclasses at class creation time. FastAPI's
optional handler adapter maps the shared not-found, multiple-results, and
database-failure classes to safe HTTP responses.

After a failed flush or SQL statement, the caller must roll back its session
before reuse. A failure may be an underlying SQLAlchemy ``IntegrityError`` or
another database exception; do not assume all backend errors are wrapped in
one package-specific exception in every path.
