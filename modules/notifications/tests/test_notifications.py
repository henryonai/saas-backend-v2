from testkit.http import claims

ALICE = claims("alice", "t1", "owner")


def test_order_created_becomes_in_app_notification(ctx):
    result = ctx.deliver("OrderCreated", {"tenant_id": "t1", "user_id": "alice",
                                          "order_id": "o1", "item": "Book"})
    assert result == {"batchItemFailures": []}

    status, notes = ctx.api("GET", "/notifications", ALICE)
    assert status == 200 and notes[0]["title"] == "Order created" and notes[0]["read"] is False

    assert ctx.api("POST", f"/notifications/{notes[0]['notification_id']}/read", ALICE)[0] == 204
    assert ctx.api("GET", "/notifications", ALICE)[1][0]["read"] is True


def test_users_only_see_their_own(ctx):
    ctx.deliver("OrderCreated", {"tenant_id": "t1", "user_id": "alice", "order_id": "o1", "item": "Book"})
    assert ctx.api("GET", "/notifications", claims("bob", "t1", "member"))[1] == []
    assert ctx.api("GET", "/notifications", claims("alice", "t2", "owner"))[1] == []


def test_mark_read_unknown_is_404(ctx):
    assert ctx.api("POST", "/notifications/nope/read", ALICE)[0] == 404


def test_invite_sends_email(ctx):
    ctx.deliver("UserInvited", {"tenant_id": "t1", "user_id": "u2", "email": "bob@example.com",
                                "invited_by": "alice@example.com", "tenant_name": "Acme"})
    to, subject, _ = ctx.email.sent[0]
    assert to == "bob@example.com" and "Acme" in subject


def test_plan_change_notifies_owner(ctx):
    ctx.deliver("SubscriptionChanged", {"tenant_id": "t1", "plan": "pro", "status": "active",
                                        "owner_id": "alice", "owner_email": "alice@example.com"})
    assert ctx.email.sent[0][:2] == ("alice@example.com", "Plan changed to pro")
    assert ctx.api("GET", "/notifications", ALICE)[1][0]["title"] == "Plan changed to pro"


def test_past_due_status_change_is_silent(ctx):
    ctx.deliver("SubscriptionChanged", {"tenant_id": "t1", "plan": "pro", "status": "past_due",
                                        "owner_id": "alice", "owner_email": "alice@example.com"})
    assert ctx.email.sent == []


def test_payment_failed_without_owner_email_still_notifies_in_app(ctx):
    ctx.deliver("PaymentFailed", {"tenant_id": "t1", "owner_id": "alice", "owner_email": ""})
    assert ctx.email.sent == []
    assert ctx.api("GET", "/notifications", ALICE)[1][0]["title"] == "Payment failed"


def test_unknown_event_ignored(ctx):
    assert ctx.deliver("SomethingElse", {"tenant_id": "t1"}) == {"batchItemFailures": []}


def test_only_the_broken_message_is_retried(ctx):
    result = ctx.deliver_batch(
        ("OrderCreated", {"tenant_id": "t1"}),  # broken: missing fields
        ("OrderCreated", {"tenant_id": "t1", "user_id": "alice", "order_id": "o1", "item": "Book"}),
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert len(ctx.api("GET", "/notifications", ALICE)[1]) == 1  # the good one went through
