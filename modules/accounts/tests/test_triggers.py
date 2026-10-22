from accounts.pre_token import add_claims
from accounts.subscriptions import on_subscription_changed
from testkit.http import claims


def login(service, user_id: str) -> dict:
    event = {"request": {"userAttributes": {"sub": user_id}}, "response": {}}
    return add_claims(event, service)["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"]


def test_login_adds_tenant_role_and_plan(ctx, owner):
    assert login(ctx.service, "alice") == {
        "tenant_id": owner["tenant_id"], "role": "owner", "plan": "free",
    }


def test_login_without_workspace_adds_nothing(ctx):
    assert login(ctx.service, "nobody") == {}


def test_subscription_change_updates_plan_and_limits(ctx, owner):
    tenant_id = owner["tenant_id"]
    on_subscription_changed(
        {"detail": {"tenant_id": tenant_id, "plan": "pro", "status": "active"}}, ctx.service,
    )

    assert login(ctx.service, "alice")["plan"] == "pro"   # next token has the new plan
    for name in ("bob", "carol", "dave"):                  # Pro allows more than 3 users
        assert ctx.api("POST", "/users/invite", owner, {"email": f"{name}@example.com"})[0] == 201


def test_subscription_change_for_unknown_tenant_is_ignored(ctx):
    on_subscription_changed(
        {"detail": {"tenant_id": "missing", "plan": "pro", "status": "active"}}, ctx.service,
    )
    assert ctx.api("GET", "/users", claims("x"))[0] == 403
