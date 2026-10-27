"""
BillingService's own rules, whatever the provider: who may pay, and webhooks
applied once, in order, without overwriting each other (through Stripe's webhooks).
"""
import pytest

from billing import webhook
from billing.repository import SubscriptionRepository
from testkit.http import claims
from testkit.logs import captured_logs


def plan_and_status(ctx, owner):
    summary = ctx.api("GET", "/billing", owner)[1]
    return summary["plan"], summary["status"]


def test_new_tenant_is_on_free(ctx, owner):
    status, summary = ctx.api("GET", "/billing", owner)
    assert status == 200 and summary["plan"] == "free" and summary["limits"]["max_users"] == 3
    assert summary["manageable"] is False   # no Stripe customer yet


def test_only_owner_can_checkout(ctx, owner):
    assert ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})[0] == 200
    assert ctx.api("POST", "/billing/checkout", claims("bob", "t1", "admin"), {"plan": "pro"})[0] == 403


def test_unknown_plan_rejected(ctx, owner):
    assert ctx.api("POST", "/billing/checkout", owner, {"plan": "gold"})[0] == 422


def test_webhook_upgrades_and_publishes_owner_details(ctx, owner):
    ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})  # records the owner
    ctx.stripe.subscribe(status="active")
    assert ctx.webhook("customer.subscription.created") == 200

    assert plan_and_status(ctx, owner) == ("pro", "active")
    [event] = ctx.events()
    assert event["type"] == "SubscriptionChanged"
    detail = {k: v for k, v in event["detail"].items() if k not in ("event_id", "occurred_at")}
    assert detail == {"tenant_id": "t1", "plan": "pro", "status": "active", "revision": 1, "comped": False,
                      "owner_id": "alice", "owner_email": "alice@example.com"}


def test_a_repeated_webhook_is_applied_once(ctx, owner):
    # Stripe sends it again, e.g. the first attempt saved everything, then timed out before answering.
    ctx.stripe.subscribe(status="active")
    assert ctx.webhook(event_id="evt_1", created=1_700_000_000) == 200
    assert ctx.webhook(event_id="evt_1", created=1_700_000_000) == 200
    assert plan_and_status(ctx, owner) == ("pro", "active")
    assert ctx.event_types() == ["SubscriptionChanged"]   # saved once, published by the relay


def test_late_retry_cannot_undo_a_newer_event(ctx, owner):
    ctx.stripe.subscribe(status="canceled")
    ctx.webhook(event_id="evt_cancel", created=200)
    ctx.stripe.subscribe(status="active")   # what an older event's retry would read
    assert ctx.webhook(event_id="evt_activate", created=100) == 200
    assert plan_and_status(ctx, owner) == ("free", "cancelled")
    assert ctx.event_types() == ["SubscriptionChanged"]


def test_events_in_the_same_second_apply_in_arrival_order(ctx, owner):
    ctx.stripe.subscribe(status="canceled")
    ctx.webhook(event_id="evt_b", created=100)
    ctx.stripe.subscribe(status="active", price="price_business")
    ctx.webhook(event_id="evt_a", created=100)
    assert plan_and_status(ctx, owner) == ("business", "active")


def test_concurrent_webhooks_for_one_tenant_retry_instead_of_overwriting(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    stale = SubscriptionRepository(ctx.table).get("t1")

    class StaleRead(SubscriptionRepository):
        def get(self, tenant_id):
            return stale

    ctx.stripe.subscribe(status="active", price="price_business")
    ctx.webhook()                                  # lands while the next one is in flight
    racing = ctx.service.__class__(StaleRead(ctx.table), ctx.service.provider)
    assert ctx.webhook(using=racing) == 503        # Stripe retries later
    assert plan_and_status(ctx, owner) == ("business", "active")


def test_an_unexpected_webhook_error_is_logged_then_retried_by_the_provider(ctx, owner):
    class Broken:
        def handle_webhook(self, headers, raw_body):
            raise RuntimeError("DynamoDB unavailable")

    with captured_logs(webhook.logger) as logs, pytest.raises(RuntimeError):   # 500: Stripe retries
        ctx.webhook(using=Broken())
    [line] = logs()
    assert line["message"] == "Webhook failed" and "DynamoDB unavailable" in line["exception"]


def test_payment_failed_then_cancelled(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    ctx.stripe.subscribe(status="past_due")
    ctx.webhook()
    assert plan_and_status(ctx, owner) == ("pro", "past_due")
    assert sorted(ctx.event_types()) == ["PaymentFailed", "SubscriptionChanged", "SubscriptionChanged"]

    ctx.stripe.subscribe(status="canceled")
    ctx.webhook("customer.subscription.deleted")
    assert plan_and_status(ctx, owner) == ("free", "cancelled")
    revisions = [e["detail"]["revision"] for e in ctx.events() if e["type"] == "SubscriptionChanged"]
    assert sorted(revisions) == [1, 2, 3]


def test_failed_save_can_be_retried(ctx, owner):
    class BrokenTable:
        name = ctx.table.name

        class meta:
            class client:
                @staticmethod
                def transact_write_items(**kwargs):
                    raise RuntimeError("DynamoDB is down")

        def get_item(self, **kwargs):
            return {}

    ctx.stripe.subscribe(status="active")
    broken = ctx.service.__class__(SubscriptionRepository(BrokenTable()), ctx.service.provider)
    with pytest.raises(RuntimeError):
        ctx.webhook(event_id="evt_9", created=1_700_000_000, using=broken)

    assert ctx.webhook(event_id="evt_9", created=1_700_000_000) == 200   # Stripe's retry is applied
    assert ctx.event_types() == ["SubscriptionChanged"]
