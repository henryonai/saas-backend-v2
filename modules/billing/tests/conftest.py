"""
A real BillingService with StripePaymentProvider, on a fake table. A fake
stands in for Stripe's API, but webhook signatures are checked by Stripe's own code.
"""
import hashlib
import hmac
import itertools
import json
import time
from types import SimpleNamespace

import pytest
import stripe

from billing.container import get_billing_service
from billing.handler import app, handler
from billing.repository import SubscriptionRepository
from billing.service import BillingService
from billing.stripe_provider import StripeConfig, StripePaymentProvider
from billing.webhook import process_webhook
from testkit.aws import create_table
from testkit.http import call, claims
from testkit.outbox import outbox_events

SECRET = "whsec_test"
CONFIG = StripeConfig(prices={"pro": "price_pro", "business": "price_business"},
                      plan_for_price={"price_pro": "pro", "price_pro_2025": "pro", "price_business": "business"},
                      portal_configuration="bpc_1")


class FakeStripe:
    """The parts of StripeClient the provider uses. Subscriptions are what Stripe would return now."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.subscriptions: dict[str, dict] = {}
        self.down = False
        sessions = lambda kind, url: SimpleNamespace(create=lambda params: self._record(kind, params, url))  # noqa: E731
        self.v1 = SimpleNamespace(
            checkout=SimpleNamespace(sessions=sessions("checkout", "https://checkout.stripe.test/c")),
            billing_portal=SimpleNamespace(sessions=sessions("portal", "https://billing.stripe.test/p")),
            subscriptions=SimpleNamespace(retrieve=self._retrieve, cancel=self._cancel, update=self._update),
        )

    def _record(self, kind, params, url):
        self._check()
        self.calls.append((kind, params))
        return SimpleNamespace(url=url)

    def _retrieve(self, subscription_id):
        self._check()
        return stripe.Subscription.construct_from(self.subscriptions[subscription_id], "sk_test")

    def _cancel(self, subscription_id):
        self._check()
        self.calls.append(("cancel", {"subscription": subscription_id}))
        self.subscriptions[subscription_id]["status"] = "canceled"

    def _update(self, subscription_id, params):
        self._check()
        self.calls.append(("update", {"subscription": subscription_id, **params}))
        self.subscriptions[subscription_id].update(params)

    def _check(self):
        if self.down:
            raise stripe.APIConnectionError("Stripe is unreachable")

    def construct_event(self, payload, sig_header, secret):
        return stripe.Webhook.construct_event(payload, sig_header, secret)

    def subscribe(self, status="active", price="price_pro", tenant="t1", sub_id="sub_1"):
        self.subscriptions[sub_id] = {
            "id": sub_id, "object": "subscription", "status": status, "customer": "cus_1",
            "cancel_at_period_end": False,
            "metadata": {"tenant_id": tenant} if tenant else {},
            "items": {"object": "list", "data": [{"id": "si_1", "price": {"id": price}}]},
        }


def signature(raw: str, secret: str = SECRET, at: int | None = None) -> str:
    at = int(time.time()) if at is None else at
    return f"t={at},v1=" + hmac.new(secret.encode(), f"{at}.{raw}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def ctx():
    table = create_table("billing-test")
    fake = FakeStripe()
    service = BillingService(SubscriptionRepository(table), StripePaymentProvider(
        client=lambda: fake, config=lambda: CONFIG, webhook_secret=lambda: SECRET, app_url=lambda: "http://app.test"))
    app.dependency_overrides[get_billing_service] = lambda: service
    ids, clock = itertools.count(1), itertools.count(1_700_000_000)

    def webhook(event_type="customer.subscription.updated", sub_id="sub_1", sign=signature,
                event_id: str | None = None, created: int | None = None, using=None) -> int:
        """Stripe's webhook for the subscription; Stripe's API then says what it is now (ctx.stripe)."""
        raw = json.dumps({"id": event_id or f"evt_{next(ids)}", "object": "event", "type": event_type,
                          "created": next(clock) if created is None else created,
                          "data": {"object": {"id": sub_id, "object": "subscription"}}})
        event = {"headers": {"Stripe-Signature": sign(raw)}, "body": raw, "isBase64Encoded": False}
        return process_webhook(event, using or service)["statusCode"]

    yield SimpleNamespace(stripe=fake, webhook=webhook, table=table, service=service, signature=signature,
                          api=lambda *a, **kw: call(handler, *a, **kw),
                          events=lambda: outbox_events(table),
                          event_types=lambda: [e["type"] for e in outbox_events(table)])
    app.dependency_overrides.clear()


@pytest.fixture
def owner():
    return claims("alice", "t1", "owner")
