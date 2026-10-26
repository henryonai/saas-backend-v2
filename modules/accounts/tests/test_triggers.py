import json

from accounts.subscriptions import process_batch
from testkit.http import FakeContext, claims
from testkit.sqs import sqs_event


def subscription_changed(ctx, tenant_id: str, plan: str, revision: int, status: str = "active") -> dict:
    detail = {"tenant_id": tenant_id, "plan": plan, "status": status, "revision": revision}
    return process_batch(sqs_event(("SubscriptionChanged", detail)), FakeContext(), ctx.service)


def test_login_lists_every_workspace_with_role_and_plan(ctx, owner, join):
    _, other = ctx.api("POST", "/tenants", claims("bob"), {"name": "Bob's shop"})
    join("bob")                                       # bob is also a member of alice's Acme

    claim = ctx.token("bob")["workspaces"]
    assert isinstance(claim, str)                     # the V1 trigger can only add string claims
    assert json.loads(claim) == {other["tenant_id"]: {"role": "owner", "plan": "free"},
                                 owner["tenant_id"]: {"role": "member", "plan": "free"}}


def test_login_without_workspace_adds_nothing(ctx):
    assert ctx.token("nobody") == {}


def test_subscription_change_updates_plan_and_limits(ctx, owner):
    assert subscription_changed(ctx, owner["tenant_id"], "pro", revision=1) == {"batchItemFailures": []}

    assert ctx.login("alice")[owner["tenant_id"]]["plan"] == "pro"   # next token has the new plan
    for name in ("bob", "carol", "dave"):                  # Pro allows more than 3 users
        assert ctx.api("POST", "/users/invite", owner, {"email": f"{name}@example.com"})[0] == 201


def test_late_or_repeated_subscription_events_are_ignored(ctx, owner):
    subscription_changed(ctx, owner["tenant_id"], "business", revision=2)
    subscription_changed(ctx, owner["tenant_id"], "pro", revision=1)        # arrives late
    subscription_changed(ctx, owner["tenant_id"], "business", revision=2)   # delivered twice
    assert ctx.login("alice")[owner["tenant_id"]]["plan"] == "business"


def test_subscription_change_for_unknown_tenant_is_ignored(ctx):
    assert subscription_changed(ctx, "missing", "pro", revision=1) == {"batchItemFailures": []}
    assert ctx.api("GET", "/users", claims("x"))[0] == 403


def test_broken_message_is_retried_alone(ctx, owner):
    result = process_batch(sqs_event(
        ("SubscriptionChanged", {"tenant_id": owner["tenant_id"]}),   # missing fields
        ("SubscriptionChanged", {"tenant_id": owner["tenant_id"], "plan": "pro", "status": "active",
                                 "revision": 1}),
    ), FakeContext(), ctx.service)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert ctx.login("alice")[owner["tenant_id"]]["plan"] == "pro"
