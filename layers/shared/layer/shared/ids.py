"""Ids that sort by creation time, so DynamoDB returns newest or oldest first."""
from datetime import UTC, datetime
from uuid import uuid4


def sortable_id(at: datetime | str | None = None, unique: str | None = None) -> str:
    """
    <13-digit epoch milliseconds>-<suffix>. Pass `at` and `unique` to get the
    same id for the same input (e.g. one notification per event).
    """
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    millis = int((at or datetime.now(UTC)).timestamp() * 1000)
    return f"{millis:013d}-{unique or uuid4().hex[:12]}"
