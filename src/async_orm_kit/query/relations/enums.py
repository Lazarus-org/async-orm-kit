from enum import StrEnum


class JoinType(StrEnum):
    """
    SQL join types.

    Inherits from str so it can be used directly in SQL generation
    and compared with plain strings where needed.
    """

    INNER = "INNER"
    LEFT = "LEFT"

    def __str__(self) -> str:
        return self.value
