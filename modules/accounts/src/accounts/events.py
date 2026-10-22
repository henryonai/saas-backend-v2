"""
Events the accounts module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.events import Event

from accounts.models import Invitation

USER_INVITED = "UserInvited"


def user_invited(invitation: Invitation) -> Event:
    """Fat event: notifications gets everything it needs to send the email."""
    return Event(USER_INVITED, invitation.tenant_id, {
        "email": invitation.email,
        "role": invitation.role,
        "invited_by": invitation.invited_by,
        "tenant_name": invitation.tenant_name,
    })
