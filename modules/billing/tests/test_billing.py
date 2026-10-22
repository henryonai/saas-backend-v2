import pytest

from testkit.http import claims


def activate(ctx, event_id="evt_1", plan="pro"):
    return ctx.webhook({"id": event_id, "type": "subscription.activated",
                        "tenant_id": "t1", "plan": plan})


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

    assert ctx.api("GET", "/billing", owner)[1]["plan"] == "pro"
    event = ctx.publisher.last()
    assert event["type"] == "SubscriptionChanged"
    assert event["detail"] == {"tenant_id": "t1", "plan": "pro", "status": "active",
                               "owner_id": "alice", "owner_email": "alice@example.com"}


def test_duplicate_webhook_ignored(ctx, owner):
    activate(ctx)
    activate(ctx)
    assert ctx.publisher.types() == ["SubscriptionChanged"]


def test_bad_signature_rejected(ctx, owner):
    status = ctx.webhook({"id": "evt_x", "type": "subscription.activated",
                          "tenant_id": "t1", "plan": "business"}, signature="forged")
    assert status == 400
    assert ctx.api("GET", "/billing", owner)[1]["plan"] == "free"


def test_payment_failed_then_cancelled(ctx, owner):
    activate(ctx)
    ctx.webhook({"id": "evt_2", "type": "payment.failed", "tenant_id": "t1"})
    summary = ctx.api("GET", "/billing", owner)[1]
    assert (summary["plan"], summary["status"]) == ("pro", "past_due")
    assert ctx.publisher.types()[-2:] == ["SubscriptionChanged", "PaymentFailed"]

    ctx.webhook({"id": "evt_3", "type": "subscription.cancelled", "tenant_id": "t1"})
    summary = ctx.api("GET", "/billing", owner)[1]
    assert (summary["plan"], summary["status"]) == ("free", "cancelled")


def test_failed_processing_can_be_retried(ctx, owner):
    class BrokenPublisher:
        def publish(self, *args, **kwargs):
            raise RuntimeError("EventBridge is down")

    broken = ctx.build_service(BrokenPublisher())
    with pytest.raises(RuntimeError):
        ctx.webhook({"id": "evt_9", "type": "subscription.activated",
                     "tenant_id": "t1", "plan": "pro"}, using=broken)

    # The provider retries later; it is processed, not skipped as a duplicate.
    assert activate(ctx, event_id="evt_9") == 200
    assert ctx.publisher.types() == ["SubscriptionChanged"]
