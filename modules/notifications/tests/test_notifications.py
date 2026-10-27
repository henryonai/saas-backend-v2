import pytest
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError

from notifications import worker
from testkit.http import claims
from testkit.logs import captured_logs
from testkit.sqs import with_event_ids

ALICE = claims("alice", "t1", "owner")


def order_created() -> dict:
    return with_event_ids({"tenant_id": "t1", "user_id": "alice", "order_id": "o1", "item": "Book"})


def payment_failed(owner_email="alice@example.com") -> dict:
    return with_event_ids({"tenant_id": "t1", "owner_id": "alice", "owner_email": owner_email})


def notes(ctx, user=ALICE) -> list[dict]:
    return ctx.api("GET", "/notifications", user)[1]


def test_order_created_becomes_in_app_notification(ctx):
    assert ctx.deliver("OrderCreated", order_created()) == {"batchItemFailures": []}

    [note] = notes(ctx)
    assert note["title"] == "Order created" and note["read"] is False

    assert ctx.api("POST", f"/notifications/{note['notification_id']}/read", ALICE)[0] == 204
    assert notes(ctx)[0]["read"] is True


def test_users_only_see_their_own(ctx):
    ctx.deliver("OrderCreated", order_created())
    assert notes(ctx, claims("bob", "t1", "member")) == []
    assert notes(ctx, claims("alice", "t2", "owner")) == []


def test_mark_read_unknown_is_404(ctx):
    assert ctx.api("POST", "/notifications/nope/read", ALICE)[0] == 404


def test_invite_email_points_to_the_app_and_quotes_the_workspace_name(ctx):
    ctx.deliver("UserInvited", with_event_ids({"tenant_id": "t1", "email": "bob@example.com", "role": "member",
                                               "invited_by": "alice@example.com", "tenant_name": "Acme"}))
    to, subject, body = ctx.email.sent[0]
    assert to == "bob@example.com" and subject == "You've been invited to a workspace"
    assert '"Acme"' in body and "https://app.test" in body and "ignore this email" in body


def test_plan_change_notifies_owner(ctx):
    ctx.deliver("SubscriptionChanged", with_event_ids({
        "tenant_id": "t1", "plan": "pro", "status": "active", "revision": 1,
        "owner_id": "alice", "owner_email": "alice@example.com"}))
    assert ctx.email.sent[0][:2] == ("alice@example.com", "Plan changed to pro")
    assert notes(ctx)[0]["title"] == "Plan changed to pro"


def test_past_due_status_change_is_silent(ctx):
    ctx.deliver("SubscriptionChanged", with_event_ids({
        "tenant_id": "t1", "plan": "pro", "status": "past_due", "revision": 2,
        "owner_id": "alice", "owner_email": "alice@example.com"}))
    assert ctx.email.sent == []


def test_payment_failed_without_owner_email_still_notifies_in_app(ctx):
    ctx.deliver("PaymentFailed", payment_failed(owner_email=""))
    assert ctx.email.sent == []
    assert notes(ctx)[0]["title"] == "Payment failed"


def test_unknown_event_ignored(ctx):
    assert ctx.deliver("SomethingElse", with_event_ids({"tenant_id": "t1"})) == {"batchItemFailures": []}


def test_only_the_broken_message_is_retried(ctx):
    with captured_logs(worker.logger) as logs:
        result = ctx.deliver_batch(
            ("OrderCreated", with_event_ids({"tenant_id": "t1"})),  # broken: missing fields
            ("OrderCreated", order_created()),
        )
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert len(notes(ctx)) == 1  # the good one went through
    [failure] = [line for line in logs() if line["level"] == "WARNING"]   # logged, with its traceback
    assert "KeyError" in failure["exception"]


def test_repeated_delivery_sends_one_email_and_one_notification(ctx):
    event = payment_failed()
    ctx.deliver("PaymentFailed", event)
    ctx.deliver("PaymentFailed", event)   # SQS delivered the same event again
    assert len(ctx.email.sent) == 1
    assert len(notes(ctx)) == 1


def test_email_failure_is_retried_without_duplicating_the_in_app_notification(ctx):
    event = payment_failed()
    ctx.email.failures_left = 2
    for _ in range(2):                    # two failed receives (the message goes back to the queue)
        with pytest.raises(BatchProcessingError):
            ctx.deliver("PaymentFailed", event)
    ctx.deliver("PaymentFailed", event)   # third receive succeeds
    assert len(ctx.email.sent) == 1
    assert len(notes(ctx)) == 1


def order_for(user_id: str, tenant_id: str = "t1") -> dict:
    return with_event_ids({"tenant_id": tenant_id, "user_id": user_id, "order_id": "o1", "item": "Book"})


def test_a_member_who_leaves_loses_their_notifications_in_that_workspace_only(ctx):
    bob, bob_elsewhere = claims("bob", "t1", "member"), claims("bob", "t2", "owner")
    for detail in (order_for("bob"), order_for("bob", "t2"), order_created()):
        ctx.deliver("OrderCreated", detail)
    ctx.deliver("MemberRemoved", with_event_ids({"tenant_id": "t1", "user_id": "bob", "removed_by": "alice"}))
    assert notes(ctx, bob) == []
    assert len(notes(ctx, bob_elsewhere)) == 1 and len(notes(ctx)) == 1


def test_a_purged_workspace_loses_every_members_notifications(ctx):
    for detail in (order_for("bob"), order_created(), order_for("alice", "t2")):
        ctx.deliver("OrderCreated", detail)
    ctx.deliver("TenantDeleted", with_event_ids({"tenant_id": "t1", "user_ids": ["alice", "bob"]}))
    assert notes(ctx) == [] and notes(ctx, claims("bob", "t1", "member")) == []
    assert len(notes(ctx, claims("alice", "t2", "owner"))) == 1
