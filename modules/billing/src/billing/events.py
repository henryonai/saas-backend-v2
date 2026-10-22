"""
Events the billing module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.events import Event

from billing.models import Subscription

SUBSCRIPTION_CHANGED = "SubscriptionChanged"
PAYMENT_FAILED = "PaymentFailed"


def _owner(sub: Subscription) -> dict:
    # Fat events: listeners can contact the owner without asking accounts.
    return {"owner_id": sub.owner_id, "owner_email": sub.owner_email}


def subscription_changed(sub: Subscription) -> Event:
    """The full plan state. `revision` increases with every change; ignore lower ones."""
    return Event(SUBSCRIPTION_CHANGED, sub.tenant_id,
                 {"plan": sub.plan, "status": sub.status, "revision": sub.revision, **_owner(sub)})


def payment_failed(sub: Subscription) -> Event:
    return Event(PAYMENT_FAILED, sub.tenant_id, _owner(sub))
