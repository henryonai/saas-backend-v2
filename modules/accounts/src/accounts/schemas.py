"""
What the accounts HTTP API accepts and returns (its contract with clients,
also the source of the OpenAPI document). Stored data is in models.py.

Responses that are identical to a model (Member, Invitation) return the model
directly; add an ...Out schema here as soon as they need to differ.
"""
from typing import Literal

from pydantic import BaseModel, Field

from shared.auth import Role

from accounts.models import Member, Tenant


# ---------- requests ----------

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InviteRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    role: Literal["admin", "member"] = "member"  # exactly one owner per tenant


class RoleUpdate(BaseModel):
    role: Literal["admin", "member"]


# ---------- responses ----------

class TenantOut(BaseModel):
    """A workspace as clients see it (no internal bookkeeping fields)."""
    tenant_id: str
    name: str
    owner_id: str
    plan: str
    subscription_status: str
    seats_used: int
    created_at: str

    @classmethod
    def of(cls, tenant: Tenant) -> "TenantOut":
        return cls.model_validate(tenant.model_dump())


class WorkspaceOut(BaseModel):
    """One of the caller's workspaces, with their role there (for a workspace switcher)."""
    tenant_id: str
    name: str
    role: Role
    plan: str

    @classmethod
    def of(cls, tenant: Tenant, member: Member) -> "WorkspaceOut":
        return cls(tenant_id=tenant.tenant_id, name=tenant.name, role=member.role, plan=tenant.plan)
