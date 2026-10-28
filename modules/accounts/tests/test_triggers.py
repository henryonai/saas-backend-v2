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


def test_a_suspended_workspace_leaves_every_token_until_it_is_unsuspended(ctx, owner, join):
    join("bob")
    tenant_id = owner["tenant_id"]
    suspended = ("TenantSuspended", {"tenant_id": tenant_id, "reason": "abuse", "event_id": "e1"})
    assert process_batch(sqs_event(suspended, suspended), FakeContext(), ctx.service) == {"batchItemFailures": []}
    assert ctx.login("alice") == {} and ctx.login("bob") == {}
    _, mine = ctx.api("GET", "/tenants", claims("alice"))
    assert [w["status"] for w in mine] == ["suspended"]
    assert ctx.api("DELETE", "/tenants/me", owner, {"name": "Acme"})[0] == 409   # can't delete it meanwhile

    process_batch(sqs_event(("TenantUnsuspended", {"tenant_id": tenant_id, "event_id": "e2"})), FakeContext(),
                  ctx.service)
    assert set(ctx.login("bob")) == {tenant_id}


def test_a_suspended_workspace_takes_no_changes_from_tokens_issued_before(ctx, owner, join):
    """The tokens below still list it (up to 15 minutes): accounts checks the workspace itself."""
    join("bob")
    carol = join("carol")
    tenant_id = owner["tenant_id"]
    process_batch(sqs_event(("TenantSuspended", {"tenant_id": tenant_id, "reason": "abuse", "event_id": "e1"})),
                  FakeContext(), ctx.service)
    status, body = ctx.api("POST", "/users/invite", owner, {"email": "dave@example.com"})
    assert status == 403 and "suspended" in body["message"]
    assert ctx.api("PATCH", "/users/bob/role", owner, {"role": "admin"})[0] == 403
    assert ctx.api("DELETE", "/users/bob", owner)[0] == 403
    assert ctx.api("POST", "/exports/workspace", owner)[0] == 403
    assert ctx.api("GET", "/users", owner)[0] == 200                  # reading still works
    assert ctx.api("DELETE", "/users/carol", carol)[0] == 204         # and a member can still leave
