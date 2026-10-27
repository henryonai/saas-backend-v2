"""
Events the accounts module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.events import Event

from accounts.models import Invitation, Tenant

USER_INVITED = "UserInvited"
MEMBER_REMOVED = "MemberRemoved"
TENANT_DELETED = "TenantDeleted"


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
