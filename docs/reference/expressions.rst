Expression and aggregate API reference
======================================

Boolean expressions and field references
----------------------------------------

.. autoclass:: async_orm_kit.Q
   :members:

.. autoclass:: async_orm_kit.F
   :members:

.. autoclass:: async_orm_kit.FExpression
   :members:

.. autoclass:: async_orm_kit.Value
   :members:

.. autoclass:: async_orm_kit.When
   :members:

.. autoclass:: async_orm_kit.Case
   :members:

Built-in and custom aggregates
------------------------------

.. autoclass:: async_orm_kit.Aggregate
   :members:

.. autoclass:: async_orm_kit.AggregateFunction
   :members:

.. autoclass:: async_orm_kit.Count
   :members:

.. autoclass:: async_orm_kit.Sum
   :members:

.. autoclass:: async_orm_kit.Avg
   :members:

.. autoclass:: async_orm_kit.Min
   :members:

.. autoclass:: async_orm_kit.Max
   :members:

Scalar SQL functions
--------------------

.. autoclass:: async_orm_kit.Function
   :members:

.. autoclass:: async_orm_kit.Cast
   :members:

.. autoclass:: async_orm_kit.Coalesce
   :members:

.. autoclass:: async_orm_kit.Concat
   :members:

.. autoclass:: async_orm_kit.Lower
   :members:

.. autoclass:: async_orm_kit.Upper
   :members:

.. autoclass:: async_orm_kit.Trim
   :members:

.. autoclass:: async_orm_kit.Length
   :members:

.. autoclass:: async_orm_kit.Extract
   :members:

.. autoclass:: async_orm_kit.NullIf
   :members:

.. autoclass:: async_orm_kit.Round
   :members:

.. autoclass:: async_orm_kit.Abs
   :members:

Window functions
----------------

.. autoclass:: async_orm_kit.Window
   :members:

.. autoclass:: async_orm_kit.RowNumber
   :members:

.. autoclass:: async_orm_kit.Rank
   :members:

.. autoclass:: async_orm_kit.DenseRank
   :members:

.. autoclass:: async_orm_kit.Lag
   :members:

.. autoclass:: async_orm_kit.Lead
   :members:

Correlation and subqueries
--------------------------

.. autoclass:: async_orm_kit.OuterRef
   :members:

.. autoclass:: async_orm_kit.Subquery
   :members:

.. autoclass:: async_orm_kit.Exists
   :members:

JSON and ARRAY
--------------

.. autoclass:: async_orm_kit.JSONPath
   :members:

.. autoclass:: async_orm_kit.JSONContains
   :members:

.. autoclass:: async_orm_kit.JSONHasKey
   :members:

.. autoclass:: async_orm_kit.ArrayIndex
   :members:

.. autoclass:: async_orm_kit.ArrayContains
   :members:

.. autoclass:: async_orm_kit.ArrayOverlap
   :members:

.. autoclass:: async_orm_kit.ArrayLength
   :members:

Use :doc:`../advanced/expressions`, :doc:`../advanced/aggregation`,
:doc:`../advanced/subqueries` and :doc:`../advanced/json-array` to
understand valid query contexts and dialect limitations.
