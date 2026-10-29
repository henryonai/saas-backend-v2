"""
Events the accounts module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.auth import CurrentUser
from shared.events import Event
from shared.exports import EXPORT_READY, EXPORT_REQUESTED

from accounts.models import ExportJob, Invitation, Tenant

USER_INVITED = "UserInvited"
MEMBER_REMOVED = "MemberRemoved"
TENANT_DELETED = "TenantDeleted"
USER_DELETED = "UserDeleted"
TENANT_CREATED = "TenantCreated"
MEMBER_JOINED = "MemberJoined"
ROLE_CHANGED = "RoleChanged"
INVITATION_CANCELLED = "InvitationCancelled"
TENANT_DELETION_SCHEDULED = "TenantDeletionScheduled"
TENANT_RESTORED = "TenantRestored"


def _actor(actor: CurrentUser) -> dict:
    """Who did it: the audit log shows this (an id, and the email it had then)."""
    return {"actor_id": actor.user_id, "actor_email": actor.email.lower()}


def user_invited(invitation: Invitation, actor: CurrentUser) -> Event:
    """Fat event: notifications gets everything it needs to send the email."""
    return Event(USER_INVITED, invitation.tenant_id, {
        "email": invitation.email,
        "role": invitation.role,
        "invited_by": invitation.invited_by,
        "tenant_name": invitation.tenant_name,
        **_actor(actor),
    })


def member_removed(tenant_id: str, user_id: str, removed_by: str, email: str, actor: CurrentUser) -> Event:
    """
    The member left the workspace (removed_by is themselves) or an owner or
    admin removed them: the assistant deletes their conversations there.
    """
    return Event(MEMBER_REMOVED, tenant_id,
                 {"user_id": user_id, "removed_by": removed_by, "email": email, **_actor(actor)})


def tenant_deleted(tenant: Tenant, user_ids: list[str]) -> Event:
    """
    The workspace's grace period ended and the purge job is removing it: every
    module deletes what it keeps for it. `user_ids` are its members at that
    moment (the owner included), for modules that key data by user.
    """
    return Event(TENANT_DELETED, tenant.tenant_id,
                 {"name": tenant.name, "owner_id": tenant.owner_id, "user_ids": user_ids})


def user_deleted(user_id: str, email: str) -> Event:
    """
    The user deleted their account, after leaving every workspace (each one a
    MemberRemoved). For modules that keep data per user outside a workspace;
    none does yet. Not about one workspace, so tenant_id is empty.
    """
    return Event(USER_DELETED, "", {"user_id": user_id, "email": email})


def export_requested(job: ExportJob, tenant_ids: list[str]) -> Event:
    """Every module in job.parts writes its part (shared/exports.py says how)."""
    return Event(EXPORT_REQUESTED, job.tenant_id, {
        "job_id": job.job_id, "kind": job.kind, "owner_id": job.owner_id, "user_id": job.requested_by,
        "email": job.email, "tenant_ids": tenant_ids,
    })


def export_ready(job: ExportJob) -> Event:
    """Every part is written: notifications tells the requester."""
    return Event(EXPORT_READY, job.tenant_id, {"job_id": job.job_id, "kind": job.kind,
                                                "user_id": job.requested_by, "email": job.email})


# ---------- for the audit log: who changed access to a workspace ----------

def tenant_created(tenant: Tenant, actor: CurrentUser) -> Event:
    return Event(TENANT_CREATED, tenant.tenant_id, {"name": tenant.name, **_actor(actor)})


def member_joined(tenant_id: str, actor: CurrentUser, role: str) -> Event:
    """The invited person accepted: they are the actor."""
    return Event(MEMBER_JOINED, tenant_id, {"user_id": actor.user_id, "role": role, **_actor(actor)})


def role_changed(tenant_id: str, user_id: str, email: str, role: str, actor: CurrentUser) -> Event:
    return Event(ROLE_CHANGED, tenant_id, {"user_id": user_id, "email": email, "role": role, **_actor(actor)})


def invitation_cancelled(tenant_id: str, email: str, actor: CurrentUser, declined: bool) -> Event:
    """Cancelled by an owner or admin, or declined by the invited person (declined)."""
    return Event(INVITATION_CANCELLED, tenant_id, {"email": email, "declined": declined, **_actor(actor)})


def tenant_deletion_scheduled(tenant: Tenant, delete_after: int, actor: CurrentUser) -> Event:
    """
    Its owner deleted the workspace: it can be restored until delete_after
    (epoch seconds), then it is purged (TenantDeleted). Billing stops the
    subscription renewing meanwhile.
    """
    return Event(TENANT_DELETION_SCHEDULED, tenant.tenant_id,
                 {"name": tenant.name, "delete_after": delete_after, **_actor(actor)})


def tenant_restored(tenant_id: str, actor: CurrentUser) -> Event:
    """Its owner restored a deleted workspace before it was purged."""
    return Event(TENANT_RESTORED, tenant_id, _actor(actor))
