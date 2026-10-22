from typing import Literal

from pydantic import BaseModel


class Subscription(BaseModel):
    tenant_id: str
    plan: str = "free"
    status: str = "active"  # active / past_due / cancelled
    # Copied from the token when the owner starts checkout, so billing events
    # can include owner details without asking the accounts module.
    owner_id: str = ""
    owner_email: str = ""
    # Increases with every change; listeners use it to ignore late events.
    revision: int = 0
    # The provider event that last set each field, so older or repeated
    # webhooks don't overwrite newer information (see service.py).
    plan_changed_at: int = 0
    plan_event_id: str = ""
    status_changed_at: int = 0
    status_event_id: str = ""


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "business"]


class CheckoutResponse(BaseModel):
    checkout_url: str


class BillingSummary(BaseModel):
    plan: str
    plan_name: str
    price_usd_month: int
    status: str
    limits: dict
