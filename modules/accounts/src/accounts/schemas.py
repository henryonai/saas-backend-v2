"""
What the accounts HTTP API accepts and returns (its contract with clients,
also the source of the OpenAPI document). Stored data is in models.py.

Responses that are identical to a model (Member, Invitation) return the model
directly; add an ...Out schema here as soon as they need to differ.
"""
from typing import Literal

from pydantic import BaseModel, Field

from shared.auth import Role

from accounts.models import ExportJob, Member, Tenant, TenantStatus


# ---------- requests ----------

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TenantDelete(BaseModel):
    """The workspace's name, typed again to confirm."""
    name: str = Field(min_length=1, max_length=100)


class AccountDelete(BaseModel):
    """The account's email address, typed again to confirm."""
    email: str = Field(min_length=3, max_length=254)


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
    status: TenantStatus
    delete_after: int | None = None   # deleting: epoch seconds until which its owner can restore it

    @classmethod
    def of(cls, tenant: Tenant) -> "TenantOut":
        return cls.model_validate(tenant.model_dump())


class WorkspaceOut(BaseModel):
    """One of the caller's workspaces, with their role there (for a workspace switcher)."""
    tenant_id: str
    name: str
    role: Role
    plan: str
    status: TenantStatus = "active"   # deleting: only its owner may restore it; the token doesn't list it
    delete_after: int | None = None

    @classmethod
    def of(cls, tenant: Tenant, member: Member) -> "WorkspaceOut":
        return cls(tenant_id=tenant.tenant_id, name=tenant.name, role=member.role, plan=tenant.plan,
                   status=tenant.status, delete_after=tenant.delete_after)


class ExportOut(BaseModel):
    """An export and how far it got; `files` has a short-lived link per part once it is done."""
    job_id: str
    kind: Literal["workspace", "user"]
    status: Literal["running", "done"]
    parts: list[str]
    done: list[str]
    created_at: str
    expires_at: int
    files: dict[str, str] = {}

    @classmethod
    def of(cls, job: ExportJob, files: dict[str, str] | None = None) -> "ExportOut":
        return cls(**job.model_dump(include={"job_id", "kind", "status", "parts", "done", "created_at", "expires_at"}),
                   files=files or {})
