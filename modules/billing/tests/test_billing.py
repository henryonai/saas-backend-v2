import pytest

from billing.repository import SubscriptionRepository
from testkit.http import claims


def activate(ctx, event_id="evt_1", plan="pro", using=None):
    return ctx.webhook({"id": event_id, "type": "subscription.activated",
                        "tenant_id": "t1", "plan": plan}, using=using)


def plan_and_status(ctx, owner):
    summary = ctx.api("GET", "/billing", owner)[1]
    return summary["plan"], summary["status"]


def test_new_tenant_is_on_free(ctx, owner):
    status, summary = ctx.api("GET", "/billing", owner)
    assert status == 200 and summary["plan"] == "free" and summary["limits"]["max_users"] == 3


def test_only_owner_can_checkout(ctx, owner):
    status, body = ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})
    assert status == 200 and "plan=pro" in body["checkout_url"]
    assert ctx.api("POST", "/billing/checkout", claims("bob", "t1", "admin"), {"plan": "pro"})[0] == 403


def test_unknown_plan_rejected(ctx, owner):
    assert ctx.api("POST", "/billing/checkout", owner, {"plan": "gold"})[0] == 422


def test_webhook_upgrades_and_publishes_owner_details(ctx, owner):
    ctx.api("POST", "/billing/checkout", owner, {"plan": "pro"})  # records the owner
    assert activate(ctx) == 200

    assert plan_and_status(ctx, owner) == ("pro", "active")
    [event] = ctx.events()
    assert event["type"] == "SubscriptionChanged"
    detail = {k: v for k, v in event["detail"].items() if k not in ("event_id", "occurred_at")}
    assert detail == {"tenant_id": "t1", "plan": "pro", "status": "active", "revision": 1,
                      "owner_id": "alice", "owner_email": "alice@example.com"}


def test_duplicate_webhook_ignored(ctx, owner):
    payload = {"id": "evt_1", "type": "subscription.activated", "tenant_id": "t1",
               "plan": "pro", "created": 1_700_000_000}
    assert ctx.webhook(payload) == 200
    assert ctx.webhook(payload) == 200
    assert ctx.event_types() == ["SubscriptionChanged"]


def test_late_retry_cannot_undo_a_newer_event(ctx, owner):
    # activated (t=100) fails once; cancelled (t=200) succeeds; then activated is retried.
    ctx.webhook({"id": "evt_cancel", "type": "subscription.cancelled", "tenant_id": "t1",
                 "created": 200})
    assert ctx.webhook({"id": "evt_activate", "type": "subscription.activated", "tenant_id": "t1",
                        "plan": "pro", "created": 100}) == 200
    assert plan_and_status(ctx, owner) == ("free", "cancelled")
    assert ctx.event_types() == ["SubscriptionChanged"]


def test_late_activation_still_sets_the_plan_after_a_newer_payment_failure(ctx, owner):
    ctx.webhook({"id": "evt_failed", "type": "payment.failed", "tenant_id": "t1", "created": 200})
    ctx.webhook({"id": "evt_activate", "type": "subscription.activated", "tenant_id": "t1",
                 "plan": "pro", "created": 100})
    assert plan_and_status(ctx, owner) == ("pro", "past_due")   # what the provider says now


def test_events_in_the_same_second_apply_in_arrival_order(ctx, owner):
    ctx.webhook({"id": "evt_b", "type": "subscription.cancelled", "tenant_id": "t1", "created": 100})
    ctx.webhook({"id": "evt_a", "type": "subscription.activated", "tenant_id": "t1",
                 "plan": "business", "created": 100})
    assert plan_and_status(ctx, owner) == ("business", "active")


def test_retry_after_a_crash_is_not_lost_or_doubled(ctx, owner):
    # The first attempt saved everything, then the Lambda timed out before answering.
    payload = {"id": "evt_1", "type": "subscription.activated", "tenant_id": "t1",
               "plan": "pro", "created": 1_700_000_000}
    ctx.webhook(payload)
    assert ctx.webhook(payload) == 200            # the provider's retry
    assert plan_and_status(ctx, owner) == ("pro", "active")
    assert ctx.event_types() == ["SubscriptionChanged"]   # saved once, published by the relay


def test_concurrent_webhooks_for_one_tenant_retry_instead_of_overwriting(ctx, owner):
    activate(ctx, "evt_1")
    stale = SubscriptionRepository(ctx.table).get("t1")

    class StaleRead(SubscriptionRepository):
        def get(self, tenant_id):
            return stale

    activate(ctx, "evt_2", plan="business")       # lands while the next one is in flight
    racing = ctx.service.__class__(StaleRead(ctx.table), ctx.service.provider)
    assert activate(ctx, "evt_3", using=racing) == 503   # provider retries later
    assert plan_and_status(ctx, owner) == ("business", "active")


def test_bad_signature_rejected(ctx, owner):
    status = ctx.webhook({"id": "evt_x", "type": "subscription.activated",
                          "tenant_id": "t1", "plan": "business"}, signature="forged")
    assert status == 400
    assert ctx.webhook({"id": "evt_y", "type": "subscription.activated",
                        "tenant_id": "t1", "plan": "pro"}, signature="tést") == 400
    assert plan_and_status(ctx, owner) == ("free", "active")


@pytest.mark.parametrize("plan", ["enterprise", None])
def test_unknown_or_missing_plan_is_rejected(ctx, owner, plan):
    assert activate(ctx, plan=plan) == 400
    assert plan_and_status(ctx, owner) == ("free", "active")
    assert ctx.events() == []


def test_malformed_webhook_is_rejected(ctx, owner):
    assert ctx.webhook({"type": "subscription.activated"}) == 400   # no id / tenant_id


def test_payment_failed_then_cancelled(ctx, owner):
    activate(ctx)
    ctx.webhook({"id": "evt_2", "type": "payment.failed", "tenant_id": "t1"})
    assert plan_and_status(ctx, owner) == ("pro", "past_due")
    assert sorted(ctx.event_types()) == ["PaymentFailed", "SubscriptionChanged", "SubscriptionChanged"]

    ctx.webhook({"id": "evt_3", "type": "subscription.cancelled", "tenant_id": "t1"})
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

    broken = ctx.service.__class__(SubscriptionRepository(BrokenTable()), ctx.service.provider)
    with pytest.raises(RuntimeError):
        activate(ctx, "evt_9", using=broken)

    assert activate(ctx, "evt_9") == 200   # the provider's retry is applied
    assert ctx.event_types() == ["SubscriptionChanged"]
