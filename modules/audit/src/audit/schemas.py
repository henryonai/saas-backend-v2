"""What the audit HTTP API returns (its contract with clients)."""
from pydantic import BaseModel

from audit.models import AuditEntry


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: str | None = None   # pass back as ?cursor= to get the next page
