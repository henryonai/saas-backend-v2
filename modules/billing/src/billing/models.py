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
