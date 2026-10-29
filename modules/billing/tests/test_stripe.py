"""
What is Stripe's own in StripePaymentProvider: Checkout, the portal, reading a
subscription back, signatures, renewals and cancelling (the fixture is in conftest.py).
"""
import time

import pytest
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError

from shared.dynamo import ConditionFailed

from billing.listener import process_batch as process_listener
from billing.repository import SubscriptionRepository
from testkit.http import FakeContext, claims
from testkit.sqs import sqs_event, with_event_ids


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


@pytest.mark.parametrize("signed", [
    {"secret": "whsec_other"},                 # wrong secret
    {"at": int(time.time()) - 600},            # older than 5 minutes
    None,                                      # no signature
])
def test_webhooks_need_a_fresh_valid_signature(ctx, signed):
    ctx.stripe.subscribe()
    sign = (lambda raw: ctx.signature(raw, **signed)) if signed else (lambda raw: "")
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


def test_purging_a_deleted_workspace_cancels_its_stripe_subscription_once(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    deleted = ("TenantDeleted", with_event_ids({"tenant_id": "t1", "user_ids": ["alice"]}))
    ctx.stripe.down = True
    with pytest.raises(BatchProcessingError):                              # SQS delivers it again later
        process_listener(sqs_event(deleted), FakeContext(), ctx.service)
    assert billing(ctx, owner)["status"] == "active"                       # nothing changed yet
    ctx.stripe.down = False

    assert process_listener(sqs_event(deleted, deleted), FakeContext(), ctx.service) == {"batchItemFailures": []}
    assert [call for call in ctx.stripe.calls if call[0] == "cancel"] == [("cancel", {"subscription": "sub_1"})]
    stored = ctx.table.get_item(Key={"pk": "TENANT#t1", "sk": "SUBSCRIPTION"})["Item"]
    assert (stored["status"], stored["plan"], stored["owner_email"]) == ("cancelled", "free", "")
    assert stored["expires_at"] > stored["deleted_at"]

    types = ctx.event_types()
    assert ctx.webhook() == 200                                            # Stripe's "canceled" arrives late
    assert ctx.event_types() == types                                      # and changes nothing


def deletion_event(event_type: str, at: str) -> tuple[str, dict]:
    return event_type, with_event_ids({"tenant_id": "t1", "occurred_at": at})


def renewals(ctx) -> list[bool]:
    return [call[1]["cancel_at_period_end"] for call in ctx.stripe.calls if call[0] == "update"]


def test_a_workspace_being_deleted_stops_renewing_until_it_is_restored(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    scheduled = deletion_event("TenantDeletionScheduled", "2026-10-29T10:00:00+00:00")
    restored = deletion_event("TenantRestored", "2026-10-29T10:05:00+00:00")

    process_listener(sqs_event(scheduled, scheduled), FakeContext(), ctx.service)   # delivered twice
    assert ctx.stripe.subscriptions["sub_1"]["cancel_at_period_end"] is True
    assert renewals(ctx) == [True]                                         # set once
    types = ctx.event_types()
    assert ctx.webhook() == 200 and billing(ctx, owner)["plan"] == "pro"   # Stripe's update: still paid for
    assert ctx.event_types() == types

    process_listener(sqs_event(restored), FakeContext(), ctx.service)
    assert ctx.stripe.subscriptions["sub_1"]["cancel_at_period_end"] is False
    process_listener(sqs_event(scheduled), FakeContext(), ctx.service)     # the older event, late
    assert renewals(ctx) == [True, False]                                  # changes nothing


def test_a_restore_leaves_a_renewal_the_customer_stopped_themselves(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.stripe.subscriptions["sub_1"]["cancel_at_period_end"] = True       # cancelled in the portal earlier
    ctx.webhook()
    process_listener(sqs_event(deletion_event("TenantDeletionScheduled", "2026-10-29T10:00:00+00:00"),
                               deletion_event("TenantRestored", "2026-10-29T10:05:00+00:00")),
                     FakeContext(), ctx.service)
    assert renewals(ctx) == []
    assert ctx.stripe.subscriptions["sub_1"]["cancel_at_period_end"] is True


def test_stopping_the_renewal_is_retried_when_stripe_is_down(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    scheduled = deletion_event("TenantDeletionScheduled", "2026-10-29T10:00:00+00:00")
    ctx.stripe.down = True
    with pytest.raises(BatchProcessingError):                              # SQS delivers it again later
        process_listener(sqs_event(scheduled), FakeContext(), ctx.service)
    ctx.stripe.down = False
    process_listener(sqs_event(scheduled), FakeContext(), ctx.service)
    assert renewals(ctx) == [True]


def test_a_restore_that_lands_while_the_renewal_is_being_stopped_wins(ctx, owner):
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    restored = deletion_event("TenantRestored", "2026-10-29T10:05:00+00:00")
    stop = ctx.service.provider.stop_renewal

    def stop_then_restore_lands(sub):
        changed = stop(sub)
        ctx.service.handle_event(*restored)    # saved while Stripe was being called, before this one saved
        return changed

    ctx.service.provider.stop_renewal = stop_then_restore_lands
    ctx.service.handle_event(*deletion_event("TenantDeletionScheduled", "2026-10-29T10:00:00+00:00"))
    assert ctx.stripe.subscriptions["sub_1"]["cancel_at_period_end"] is False
    assert renewals(ctx) == [True, False]


def test_a_workspace_without_a_subscription_has_nothing_to_stop(ctx):
    ctx.service.handle_event(*deletion_event("TenantDeletionScheduled", "2026-10-29T10:00:00+00:00"))
    assert ctx.stripe.calls == []
    assert [i["sk"] for i in ctx.table.scan()["Items"]] == ["STATUS"]   # only the status copy: no subscription


def test_a_workspace_export_has_its_subscription(ctx, owner):
    from shared.exports import FakePartWriter, part_key
    ctx.service.exports = FakePartWriter()
    ctx.stripe.subscribe(status="active")
    ctx.webhook()
    requested = with_event_ids({"tenant_id": "t1", "job_id": "job-1", "kind": "workspace", "owner_id": "t1",
                                "user_id": "alice", "email": "alice@example.com", "tenant_ids": ["t1"]})
    assert process_listener(sqs_event(("ExportRequested", requested)), FakeContext(), ctx.service) == \
        {"batchItemFailures": []}
    part = ctx.service.exports.written[part_key("workspace", "t1", "job-1", "billing")]
    assert part["subscription"]["plan"] == "pro" and part["subscription"]["subscription_id"] == "sub_1"


def test_a_granted_plan_holds_until_stripe_changes_the_plan(ctx, owner):
    granted = ("PlanGranted", {**with_event_ids({"tenant_id": "t1", "plan": "business", "note": "partner",
                                                 "actor_id": "ops", "actor_email": "ops@example.com"}),
                               "occurred_at": "2023-01-01T00:00:00+00:00"})   # before the test's Stripe clock
    assert process_listener(sqs_event(granted, granted), FakeContext(), ctx.service) == {"batchItemFailures": []}
    summary = billing(ctx, owner)
    assert (summary["plan"], summary["status"]) == ("business", "active")
    assert ctx.event_types().count("SubscriptionChanged") == 1                 # once, though delivered twice
    [changed] = [e for e in ctx.events() if e["type"] == "SubscriptionChanged"]
    assert changed["detail"]["comped"] is True

    ctx.stripe.subscribe(status="active", price="price_pro")                   # they subscribe later
    ctx.webhook()
    stored = ctx.table.get_item(Key={"pk": "TENANT#t1", "sk": "SUBSCRIPTION"})["Item"]
    assert (stored["plan"], stored["comped"]) == ("pro", False)
