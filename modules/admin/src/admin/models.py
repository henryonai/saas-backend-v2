"""What the admin module stores and works with. What the API returns is in schemas.py."""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from shared.ids import sortable_id


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class WorkspaceSummary(BaseModel):
    """One workspace as the back-office sees it: kept up to date from other modules' events."""
    tenant_id: str
    name: str = ""
    owner_id: str = ""
    created_at: str = ""
    plan: str = "free"
    subscription_status: str = "active"
    comped: bool = False           # a plan an admin granted, until the payment provider changes it
    status: Literal["active", "deleting", "suspended"] = "active"
    members: int = 0
    orders: int = 0


class AdminAction(BaseModel):
    """One thing a platform admin did (the admin log)."""
    action_id: str = Field(default_factory=sortable_id)
    at: str = Field(default_factory=now_iso)
    admin_id: str
    admin_email: str
    action: Literal["grant_plan", "suspend", "unsuspend"]
    tenant_id: str
    details: dict = {}
