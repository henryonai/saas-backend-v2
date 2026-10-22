import time
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Tenant(BaseModel):
    tenant_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    owner_id: str
    # A local copy of the plan, kept up to date by SubscriptionChanged events.
    plan: str = "free"
    subscription_status: str = "active"
    subscription_revision: int = 0  # billing's revision of the plan copied here
    seats_used: int = 1  # members, limited by the plan
    created_at: str = Field(default_factory=now_iso)


class Member(BaseModel):
    tenant_id: str
    user_id: str
    email: str
    role: Literal["owner", "admin", "member"]
    created_at: str = Field(default_factory=now_iso)


INVITATION_DAYS = 7


class Invitation(BaseModel):
    """Pending until the invited person accepts or declines it, or it expires."""
    tenant_id: str
    tenant_name: str
    email: str
    role: Literal["admin", "member"]
    invited_by: str
    created_at: str = Field(default_factory=now_iso)
    expires_at: int = Field(default_factory=lambda: int(time.time()) + INVITATION_DAYS * 24 * 3600)

    def expired(self) -> bool:
        return self.expires_at <= time.time()


class InviteRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    role: Literal["admin", "member"] = "member"  # exactly one owner per tenant


class RoleUpdate(BaseModel):
    role: Literal["admin", "member"]
