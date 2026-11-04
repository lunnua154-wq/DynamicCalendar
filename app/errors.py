"""Domain exceptions for DynaCalendar.

FastAPI exception handlers map these to ``{"error": <message>}`` JSON
responses with the appropriate HTTP status code (see ``app/main.py``).
"""


class DynaCalendarError(Exception):
    """Base domain error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(DynaCalendarError):
    """Requested resource does not exist -> HTTP 404."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 404)


class ConflictError(DynaCalendarError):
    """Request conflicts with current state -> HTTP 409."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 409)


class ValidationError(DynaCalendarError):
    """Invalid input -> HTTP 422."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 422)


class IcsImportError(DynaCalendarError):
    """An .ics file could not be imported -> HTTP 422."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 422)
