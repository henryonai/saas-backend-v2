"""
Events the accounts module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.auth import CurrentUser
from shared.events import Event

from accounts.models import Invitation, Tenant

USER_INVITED = "UserInvited"
MEMBER_REMOVED = "MemberRemoved"
TENANT_DELETED = "TenantDeleted"
TENANT_DELETION_SCHEDULED = "TenantDeletionScheduled"
TENANT_RESTORED = "TenantRestored"


def _actor(actor: CurrentUser) -> dict:
    """Who did it: an id, and the email it had then."""
    return {"actor_id": actor.user_id, "actor_email": actor.email.lower()}


def user_invited(invitation: Invitation) -> Event:
    """Fat event: notifications gets everything it needs to send the email."""
    return Event(USER_INVITED, invitation.tenant_id, {
        "email": invitation.email,
        "role": invitation.role,
        "invited_by": invitation.invited_by,
        "tenant_name": invitation.tenant_name,
    })


def member_removed(tenant_id: str, user_id: str, removed_by: str) -> Event:
    """
    The member left the workspace (removed_by is themselves) or an owner or
    admin removed them: the assistant deletes their conversations there.
    """
    return Event(MEMBER_REMOVED, tenant_id, {"user_id": user_id, "removed_by": removed_by})


def tenant_deleted(tenant: Tenant, user_ids: list[str]) -> Event:
    """
    The workspace's grace period ended and the purge job is removing it: every
    module deletes what it keeps for it. `user_ids` are its members at that
    moment (the owner included), for modules that key data by user.
    """
    return Event(TENANT_DELETED, tenant.tenant_id,
                 {"name": tenant.name, "owner_id": tenant.owner_id, "user_ids": user_ids})


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
