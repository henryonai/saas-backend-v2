"""
StripePaymentProvider through the real routes and webhook handler. A fake
stands in for Stripe's API, but signatures are checked by Stripe's own code.
"""
import hashlib
import hmac
import itertools
import json
import time
from types import SimpleNamespace

import pytest
import stripe

from shared.dynamo import ConditionFailed

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
            subscriptions=SimpleNamespace(retrieve=self._retrieve),
        )

    def _record(self, kind, params, url):
        self._check()
        self.calls.append((kind, params))
        return SimpleNamespace(url=url)

    def _retrieve(self, subscription_id):
        self._check()
        return stripe.Subscription.construct_from(self.subscriptions[subscription_id], "sk_test")

    def _check(self):
        if self.down:
            raise stripe.APIConnectionError("Stripe is unreachable")

    def construct_event(self, payload, sig_header, secret):
        return stripe.Webhook.construct_event(payload, sig_header, secret)

    def subscribe(self, status="active", price="price_pro", tenant="t1", sub_id="sub_1"):
        self.subscriptions[sub_id] = {
            "id": sub_id, "object": "subscription", "status": status, "customer": "cus_1",
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
        client=lambda: fake, config=lambda: CONFIG, webhook_secret=lambda: SECRET, app_url="http://app.test"))
    app.dependency_overrides[get_billing_service] = lambda: service
    ids, clock = itertools.count(1), itertools.count(1_700_000_000)

    def webhook(event_type="customer.subscription.updated", sub_id="sub_1", sign=signature) -> int:
        raw = json.dumps({"id": f"evt_{next(ids)}", "object": "event", "type": event_type, "created": next(clock),
                          "data": {"object": {"id": sub_id, "object": "subscription"}}})
        event = {"headers": {"Stripe-Signature": sign(raw)}, "body": raw, "isBase64Encoded": False}
        return process_webhook(event, service)["statusCode"]

    yield SimpleNamespace(stripe=fake, webhook=webhook, table=table, api=lambda *a, **kw: call(handler, *a, **kw),
                          event_types=lambda: [e["type"] for e in outbox_events(table)])
    app.dependency_overrides.clear()


@pytest.fixture
def owner():
    return claims("alice", "t1", "owner")


def billing(ctx, owner) -> dict:
    return ctx.api("GET", "/billing", owner)[1]


def test_first_subscription_goes_through_checkout(ctx, owner):
    status, body = ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})
    assert status == 200 and body["checkout_url"] == "https://checkout.stripe.test/c"
    [(kind, params)] = ctx.stripe.calls
    assert kind == "checkout" and params["mode"] == "subscription"
    assert params["line_items"] == [{"price": "price_pro", "quantity": 1}]
    assert params["subscription_data"] == {"metadata": {"tenant_id": "t1"}}  # comes back with every webhook
    assert params["customer_email"] == "alice@example.com"
    assert params["success_url"] == params["cancel_url"] == "http://app.test/billing"


def test_webhook_saves_stripes_current_state(ctx, owner):
    ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})
    ctx.stripe.subscribe(status="active", price="price_pro")
    assert ctx.webhook("customer.subscription.created") == 200

    summary = billing(ctx, owner)
    assert (summary["plan"], summary["status"], summary["manageable"]) == ("pro", "active", True)
    assert ctx.event_types() == ["SubscriptionChanged"]


def test_updates_that_change_nothing_publish_nothing(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook("customer.subscription.created")
    ctx.webhook("customer.subscription.updated")   # e.g. a new invoice: same plan and status
    assert ctx.event_types() == ["SubscriptionChanged"]   # so the owner isn't told twice


def test_a_quiet_update_still_stops_an_older_concurrent_write(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook("customer.subscription.created")
    repository = SubscriptionRepository(ctx.table)
    read_earlier = repository.get("t1")                  # another webhook read the state here...
    ctx.webhook("customer.subscription.updated")          # ...then this one saved without changing anything
    with pytest.raises(ConditionFailed):                  # so the other one's write is refused (and retried)
        repository.save_change(read_earlier.model_copy(update={"status": "past_due"}), read_earlier.revision, [])
    assert repository.get("t1").status == "active"


def test_upgrading_a_subscriber_opens_the_portals_confirm_page(ctx, owner):
    ctx.stripe.subscribe(status="active", price="price_pro")
    ctx.webhook("customer.subscription.created")

    status, body = ctx.api("POST", "/billing/checkout", owner, {"plan": "business"})
    assert status == 200 and body["checkout_url"] == "https://billing.stripe.test/p"
    kind, params = ctx.stripe.calls[-1]
    assert kind == "portal" and params["customer"] == "cus_1" and params["configuration"] == "bpc_1"
    assert params["flow_data"]["type"] == "subscription_update_confirm"
    assert params["flow_data"]["subscription_update_confirm"] == {
        "subscription": "sub_1", "items": [{"id": "si_1", "price": "price_business", "quantity": 1}]}

    ctx.stripe.subscribe(status="active", price="price_business")
    ctx.webhook("customer.subscription.updated")
    assert billing(ctx, owner)["plan"] == "business"


def test_a_cancelled_customer_subscribes_again_as_the_same_customer(ctx, owner):
    ctx.stripe.subscribe(status="canceled")
    ctx.webhook("customer.subscription.deleted")
    assert (billing(ctx, owner)["plan"], billing(ctx, owner)["status"]) == ("free", "cancelled")

    ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})
    kind, params = ctx.stripe.calls[-1]
    assert kind == "checkout" and params["customer"] == "cus_1" and "customer_email" not in params


def test_failed_payment_keeps_the_plan(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook("customer.subscription.created")
    ctx.stripe.subscribe(status="past_due")
    ctx.webhook("customer.subscription.updated")
    assert (billing(ctx, owner)["plan"], billing(ctx, owner)["status"]) == ("pro", "past_due")
    assert ctx.event_types() == ["SubscriptionChanged", "SubscriptionChanged", "PaymentFailed"]


def test_older_prices_still_map_to_their_plan(ctx, owner):
    ctx.stripe.subscribe(price="price_pro_2025")   # a subscriber from before a price change
    ctx.webhook("customer.subscription.created")
    assert billing(ctx, owner)["plan"] == "pro"


def test_billing_portal(ctx, owner):
    assert ctx.api("POST", "/billing/portal", owner)[0] == 400   # no Stripe customer yet
    ctx.stripe.subscribe()
    ctx.webhook("customer.subscription.created")

    status, body = ctx.api("POST", "/billing/portal", owner)
    assert status == 200 and body["portal_url"] == "https://billing.stripe.test/p"
    assert ctx.stripe.calls[-1][1] == {"customer": "cus_1", "configuration": "bpc_1",
                                      "return_url": "http://app.test/billing"}
    assert ctx.api("POST", "/billing/portal", claims("bob", "t1", "admin"))[0] == 403


@pytest.mark.parametrize("sign", [
    lambda raw: signature(raw, secret="whsec_other"),                 # wrong secret
    lambda raw: signature(raw, at=int(time.time()) - 600),            # older than 5 minutes
    lambda raw: "",                                                   # no signature
])
def test_webhooks_need_a_fresh_valid_signature(ctx, sign):
    ctx.stripe.subscribe()
    assert ctx.webhook("customer.subscription.created", sign=sign) == 400
    assert ctx.event_types() == []


def test_other_events_and_other_subscriptions_are_ignored(ctx, owner):
    assert ctx.webhook("invoice.paid") == 200
    ctx.stripe.subscribe(tenant=None)   # created in Stripe's dashboard, not by the app
    assert ctx.webhook("customer.subscription.created") == 200
    ctx.stripe.subscribe(status="incomplete")   # first payment not done yet
    assert ctx.webhook("customer.subscription.created") == 200
    assert ctx.event_types() == []


def test_an_unknown_price_is_refused(ctx):
    ctx.stripe.subscribe(price="price_from_somewhere_else")
    assert ctx.webhook("customer.subscription.created") == 400   # Stripe shows it as failed and retries


def test_stripe_outages_are_retried(ctx, owner):
    ctx.stripe.down = True
    assert ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})[0] == 503
    ctx.stripe.subscribe()
    assert ctx.webhook("customer.subscription.created") == 503   # Stripe sends it again later


def test_the_current_plan_cant_be_bought_again(ctx, owner):
    ctx.stripe.subscribe(status="active", price="price_pro")
    ctx.webhook("customer.subscription.created")
    assert ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})[0] == 409
