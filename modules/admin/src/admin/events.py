"""
Events the admin module publishes: its public contract. Listeners match on the
name (no module imports another); change payloads only by adding fields.
"""
from shared.auth import CurrentUser
from shared.events import Event

PLAN_GRANTED = "PlanGranted"
TENANT_SUSPENDED = "TenantSuspended"
TENANT_UNSUSPENDED = "TenantUnsuspended"


def _actor(admin: CurrentUser) -> dict:
    return {"actor_id": admin.user_id, "actor_email": admin.email.lower(), "by_platform_admin": True}


def plan_granted(tenant_id: str, plan: str, note: str, admin: CurrentUser) -> Event:
    """Billing gives the workspace this plan without a payment, until the provider changes it."""
    return Event(PLAN_GRANTED, tenant_id, {"plan": plan, "note": note, **_actor(admin)})


def tenant_suspended(tenant_id: str, reason: str, admin: CurrentUser) -> Event:
    """Accounts takes the workspace out of every member's token until it is unsuspended."""
    return Event(TENANT_SUSPENDED, tenant_id, {"reason": reason, **_actor(admin)})


def tenant_unsuspended(tenant_id: str, admin: CurrentUser) -> Event:
    return Event(TENANT_UNSUSPENDED, tenant_id, _actor(admin))
