Field types and validation
==========================

``StringField`` and ``FileField`` are SQLAlchemy custom type/descriptor
integrations. Standard SQLAlchemy types (``Integer``, ``String``, ``JSON``,
``DateTime``, etc.) remain the default for ordinary fields.

StringField
-----------

``StringField(max_length=..., min_length=..., validators=...)`` adds
string-length validation around its underlying SQLAlchemy type. For example:

.. code-block:: python

   from sqlalchemy.orm import Mapped, mapped_column
   from async_orm_kit import Model, StringField

   class Customer(Model):
       __tablename__ = "customers"
       id: Mapped[int] = mapped_column(primary_key=True)
       handle: Mapped[str] = mapped_column(StringField(max_length=64))

Validators are also available from
``async_orm_kit.models.fields.validators``:
``MaxLengthValidator``, ``MinLengthValidator``, ``RangeValidator`` and
``Regex``. Refer to the actual validator signatures before defining custom
validation policies.

Model validation lifecycle
--------------------------

Override ``async def clean(self)`` for model-specific checks. The
``full_clean()`` method runs field and model validation. ``save()`` invokes
validation as part of its write path; normal SQLAlchemy calls bypassing
``Model.save()`` are not automatically passed through this helper.

.. code-block:: python

   class Customer(Model):
       # ... mapped columns ...
       async def clean(self):
           if self.handle.strip() == "":
               raise ValueError("handle must not be blank")

   async with session.begin():
       customer = Customer(handle="Ada")
       await customer.save(session)

A SQLAlchemy database constraint is still necessary for correctness under
concurrent writers. Python validation is not a replacement for unique indexes,
foreign keys, CHECK constraints or server-side authorization.

File validation
---------------

``FileField`` validates the stored relative key and rejects unsafe paths.
The optional ``async-storage-kit`` adapter validates storage interactions but
does not turn database and object storage writes into a single transaction.
See :doc:`../integrations-rst/storage`.
