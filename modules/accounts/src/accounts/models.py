from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Tenant(BaseModel):
    tenant_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    owner_id: str
    # A local copy of the plan, kept up to date by SubscriptionChanged events.
    plan: str = "free"
    subscription_status: str = "active"
    created_at: str = Field(default_factory=now_iso)


class Member(BaseModel):
    tenant_id: str
    user_id: str
    email: str
    role: Literal["owner", "admin", "member"]
    status: Literal["active", "invited"] = "active"
    created_at: str = Field(default_factory=now_iso)


class InviteRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    role: Literal["admin", "member"] = "member"  # exactly one owner per tenant


class RoleUpdate(BaseModel):
    role: Literal["admin", "member"]
