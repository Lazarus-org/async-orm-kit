class FieldValidationError(ValueError):
    """Field Validation error."""

    def __init__(
        self, message: str, field_name: str | None = None, code: str | None = None
    ):
        self.message = message
        self.field_name = field_name
        self.code = code
        super().__init__(message)
