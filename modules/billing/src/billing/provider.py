"""
Payment provider interface. The rest of billing only knows these two methods.

To go live, write one class with the same methods for your provider (Paddle,
Lemon Squeezy, Airwallex, VNPay, ...) and return it from container.py.

DevPaymentProvider fakes the checkout page but verifies webhook signatures
for real (HMAC-SHA256), like production providers do.
"""
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Protocol

from shared.errors import InvalidRequest


@dataclass
class BillingEvent:
    event_id: str
    event_type: str  # subscription.activated / subscription.cancelled / payment.failed
    tenant_id: str
    plan: str | None


class PaymentProvider(Protocol):
    def create_checkout(self, tenant_id: str, plan: str, customer_email: str) -> str: ...
    def parse_webhook(self, headers: dict, raw_body: str) -> BillingEvent: ...


class DevPaymentProvider:
    def __init__(self, webhook_secret: str, app_url: str):
        self.webhook_secret = webhook_secret
        self.app_url = app_url

    def create_checkout(self, tenant_id: str, plan: str, customer_email: str) -> str:
        # Real providers return their own page URL and carry tenant_id as
        # "metadata", which comes back in the webhook.
        return f"{self.app_url}/dev-checkout?tenant_id={tenant_id}&plan={plan}"

    def sign(self, raw_body: str) -> str:
        return hmac.new(self.webhook_secret.encode(), raw_body.encode(), hashlib.sha256).hexdigest()

    def parse_webhook(self, headers: dict, raw_body: str) -> BillingEvent:
        signature = {k.lower(): v for k, v in headers.items()}.get("x-signature", "")
        if not hmac.compare_digest(signature, self.sign(raw_body)):  # timing-safe compare
            raise InvalidRequest("Invalid webhook signature")
        data = json.loads(raw_body)
        return BillingEvent(event_id=data["id"], event_type=data["type"],
                            tenant_id=data["tenant_id"], plan=data.get("plan"))
