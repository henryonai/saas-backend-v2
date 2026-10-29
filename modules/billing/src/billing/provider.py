"""
Payment provider interface. The rest of billing only knows these methods.

StripePaymentProvider (stripe_provider.py) is the one this template uses. For another
provider (Paddle, Lemon Squeezy, Airwallex, VNPay, ...), write one class with
the same methods and return it from container.py. Map the provider's event id
and creation time to BillingEvent: billing uses them to skip repeated webhooks
and webhooks that arrive out of order. Real providers also sign a timestamp;
reject old ones to limit replays.
"""
from dataclasses import dataclass
from typing import Protocol

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

    def stop_renewal(self, sub: Subscription) -> bool:
        """
        Lets the subscription end with the period paid for, instead of renewing
        (its workspace is being deleted). False if there was nothing to stop:
        no subscription, it ended, or it was set to end already.
        """
        ...

    def resume_renewal(self, sub: Subscription) -> None:
        """Lets the subscription renew again (its workspace was restored). Safe to repeat."""
        ...
