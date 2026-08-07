"""Application-specific exception types."""


class AppError(Exception):
    """Base class for all application-raised errors."""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ConfigurationError(AppError):
    """Raised when required configuration is missing or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500)


class DatabaseUnavailableError(AppError):
    """Raised when the database connection pool cannot serve a request."""

    def __init__(self, message: str = "Database is unavailable") -> None:
        super().__init__(message, status_code=503)


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status_code=404)
