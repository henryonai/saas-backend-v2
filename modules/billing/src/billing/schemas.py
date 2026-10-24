"""
What the billing HTTP API accepts and returns (its contract with clients,
also the source of the OpenAPI document). Stored data is in models.py.
"""
from typing import Literal

from pydantic import BaseModel

from shared.plans import get_plan

from billing.models import Subscription


# ---------- requests ----------

class CheckoutRequest(BaseModel):
    plan: Literal["pro", "business"]


# ---------- responses ----------

class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class BillingSummary(BaseModel):
    """A subscription as clients see it, with the plan's price and limits."""
    plan: str
    plan_name: str
    price_usd_month: int
    status: str
    limits: dict
    manageable: bool  # the provider has a billing portal for this workspace (POST /billing/portal)

    @classmethod
    def of(cls, sub: Subscription) -> "BillingSummary":
        plan = get_plan(sub.plan)
        return cls(plan=sub.plan, plan_name=plan["name"], price_usd_month=plan["price_usd_month"],
                   status=sub.status, limits={k: v for k, v in plan.items() if k.startswith("max_")},
                   manageable=bool(sub.customer_id))
