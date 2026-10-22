"""
What the accounts module stores and works with. The repository saves these;
the service returns them. What the API accepts and returns is in schemas.py.
"""
import time
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from shared.auth import Role


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    role: Role
    created_at: str = Field(default_factory=now_iso)


class Invitation(BaseModel):
    """Pending until the invited person accepts or declines it, or it expires."""
    tenant_id: str
    tenant_name: str
    email: str
    role: Role
    invited_by: str
    created_at: str = Field(default_factory=now_iso)
    expires_at: int  # epoch seconds; set by the service from its InvitationLimits

    def expired(self) -> bool:
        return self.expires_at <= time.time()
