"""
Payment provider interface. The rest of billing only knows these methods.

StripePaymentProvider (stripe_provider.py) is the real one. For another
provider (Paddle, Lemon Squeezy, Airwallex, VNPay, ...), write one class with
the same methods and return it from container.py. Map the provider's event id
and creation time to BillingEvent: billing uses them to skip repeated webhooks
and webhooks that arrive out of order. Real providers also sign a timestamp;
reject old ones to limit replays.

DevPaymentProvider fakes the checkout page but verifies webhook signatures
for real (HMAC-SHA256), like production providers do.
"""
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from shared.errors import InvalidRequest

from billing.models import Subscription


@dataclass
class BillingEvent:
    event_id: str
    event_type: str  # subscription.activated / subscription.cancelled / payment.failed (others are ignored)
    tenant_id: str
    plan: str | None
    occurred_at: int  # when the provider created the event (epoch seconds)
    customer_id: str = ""      # the provider's ids, when it has them
    subscription_id: str = ""


class PaymentProvider(Protocol):
    def create_checkout(self, sub: Subscription, plan: str, customer_email: str) -> str: ...
    def create_portal(self, sub: Subscription) -> str: ...
    def parse_webhook(self, headers: dict, raw_body: str) -> BillingEvent: ...
    def cancel(self, sub: Subscription) -> None:
        """Ends the workspace's paid subscription now (its workspace was deleted). Safe to repeat."""
        ...


class DevPaymentProvider:
    def __init__(self, webhook_secret: Callable[[], str], app_url: str):
        # A function, so the secret is only read when a webhook arrives.
        self.webhook_secret = webhook_secret
        self.app_url = app_url

    def create_checkout(self, sub: Subscription, plan: str, customer_email: str) -> str:
        # Real providers return their own page URL and carry tenant_id as
        # "metadata", which comes back in the webhook.
        return f"{self.app_url}/dev-checkout?tenant_id={sub.tenant_id}&plan={plan}"

    def create_portal(self, sub: Subscription) -> str:
        raise InvalidRequest("The development payment provider has no billing portal")

    def cancel(self, sub: Subscription) -> None:
        pass   # nothing is charged

    def sign(self, raw_body: str) -> str:
        return hmac.new(self.webhook_secret().encode(), raw_body.encode(), hashlib.sha256).hexdigest()

    def parse_webhook(self, headers: dict, raw_body: str) -> BillingEvent:
        signature = {k.lower(): v for k, v in headers.items()}.get("x-signature", "")
        # Compare bytes: timing-safe, and works for any header value.
        if not hmac.compare_digest(signature.encode(), self.sign(raw_body).encode()):
            raise InvalidRequest("Invalid webhook signature")
        try:
            data = json.loads(raw_body)
            return BillingEvent(event_id=str(data["id"]), event_type=data["type"],
                                tenant_id=data["tenant_id"], plan=data.get("plan"),
                                occurred_at=int(data["created"]))
        except (ValueError, KeyError, TypeError) as e:
            raise InvalidRequest(f"Malformed webhook: {e}") from e
