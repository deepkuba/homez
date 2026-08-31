class AlertParseError(ValueError):
    """Raised when an alert cannot be accepted by its source contract."""

    def __init__(self, message: str, *, code: str = "invalid-alert") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")
