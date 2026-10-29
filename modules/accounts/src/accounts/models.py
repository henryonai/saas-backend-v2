"""
What the accounts module stores and works with. The repository saves these;
the service returns them. What the API accepts and returns is in schemas.py.
"""
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from shared.auth import Role
from shared.ids import sortable_id


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# active; deleting: its owner deleted it, and it can be restored until delete_after;
# purging: the purge job is removing it (purge.py); suspended: a platform admin
# stopped it (the admin module's TenantSuspended). Only active workspaces are in
# tokens. Items saved before deletion existed have no status, which reads as active.
TenantStatus = Literal["active", "deleting", "purging", "suspended"]


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
    status: TenantStatus = "active"
    delete_after: int | None = None  # epoch seconds: when the purge job may remove it (deleting only)

    @property
    def active(self) -> bool:
        return self.status == "active"


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


class ExportJob(BaseModel):
    """
    A copy of a workspace's data (kind workspace, for its owner) or of a user's
    own (kind user), which every module writes its part of (shared/exports.py).
    """
    job_id: str = Field(default_factory=sortable_id)   # sorts by time: the newest is the latest
    kind: Literal["workspace", "user"]
    owner_id: str                 # the workspace, or the user, it is a copy of
    requested_by: str
    email: str                    # who asked: ExportReady tells them
    parts: list[str]              # the modules that write one
    done: list[str] = []          # the parts written so far
    status: Literal["running", "done"] = "running"
    created_at: str = Field(default_factory=now_iso)
    expires_at: int               # TTL removes the job when the bucket removes its files

    @property
    def tenant_id(self) -> str:
        """The workspace's id for its events; "" for a user's export, which is about no single workspace."""
        return self.owner_id if self.kind == "workspace" else ""
