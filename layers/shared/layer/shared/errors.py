"""
Business errors. Services raise these; shared/http.py turns them into HTTP
responses in one place, so routes never need try/except.
"""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidRequest(AppError):
    status_code = 400


class Unauthorized(AppError):
    status_code = 401


class PlanLimitReached(AppError):
    """The plan doesn't allow this. The frontend should show 'Upgrade'."""

    status_code = 402


class Forbidden(AppError):
    status_code = 403


class NotFound(AppError):
    status_code = 404


class Conflict(AppError):
    status_code = 409


class TooManyRequests(AppError):
    status_code = 429


class ServiceBusy(AppError):
    """Temporary, e.g. too many changes to one item at once. Safe to retry."""

    status_code = 503
