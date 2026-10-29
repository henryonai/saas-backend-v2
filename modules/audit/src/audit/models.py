"""What the audit module stores: one entry per event about a workspace."""
from pydantic import BaseModel


class AuditEntry(BaseModel):
    event_id: str
    tenant_id: str
    type: str                 # the event's name, e.g. RoleChanged
    occurred_at: str
    actor_id: str = ""        # who did it ("" when the system or the payment provider did)
    actor_email: str = ""
    details: dict             # the rest of the event: what it was about
