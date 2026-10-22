from testkit.http import claims


def test_signup_creates_workspace_with_owner(ctx, owner):
    status, tenant = ctx.api("GET", "/tenants/me", owner)
    assert status == 200 and tenant["name"] == "Acme" and tenant["plan"] == "free"

    _, users = ctx.api("GET", "/users", owner)
    assert [(u["email"], u["role"]) for u in users] == [("alice@example.com", "owner")]


def test_cannot_create_second_workspace(ctx, owner):
    status, _ = ctx.api("POST", "/tenants", claims("alice"), {"name": "Another"})
    assert status == 409


def test_no_workspace_yet_is_blocked(ctx):
    status, body = ctx.api("GET", "/users", claims("bob"))
    assert status == 403 and "workspace" in body["message"]


def test_invite_publishes_fat_event(ctx, owner):
    status, member = ctx.api("POST", "/users/invite", owner, {"email": "Bob@Example.com", "role": "admin"})
    assert status == 201
    assert member["email"] == "bob@example.com" and member["status"] == "invited"
    assert ctx.identity.created == ["bob@example.com"]
    event = ctx.publisher.last()
    assert event["type"] == "UserInvited"
    assert event["detail"]["tenant_name"] == "Acme"
    assert event["detail"]["invited_by"] == "alice@example.com"


def test_duplicate_invite_rejected(ctx, owner):
    ctx.api("POST", "/users/invite", owner, {"email": "bob@example.com"})
    status, _ = ctx.api("POST", "/users/invite", owner, {"email": "bob@example.com"})
    assert status == 409


def test_member_cannot_invite(ctx, owner):
    ctx.api("POST", "/users/invite", owner, {"email": "carol@example.com"})
    carol = claims("user-carol", owner["tenant_id"], "member")
    status, _ = ctx.api("POST", "/users/invite", carol, {"email": "dave@example.com"})
    assert status == 403


def test_free_plan_user_limit(ctx, owner):
    for name in ("bob", "carol"):  # owner + 2 = Free limit of 3
        assert ctx.api("POST", "/users/invite", owner, {"email": f"{name}@example.com"})[0] == 201
    status, body = ctx.api("POST", "/users/invite", owner, {"email": "dave@example.com"})
    assert status == 402 and "Upgrade" in body["message"]


def test_invalid_email_rejected(ctx, owner):
    status, _ = ctx.api("POST", "/users/invite", owner, {"email": "not-an-email"})
    assert status == 422


def test_owner_is_protected(ctx, owner):
    assert ctx.api("PATCH", "/users/alice/role", owner, {"role": "member"})[0] == 403
    assert ctx.api("DELETE", "/users/alice", owner)[0] == 403


def test_change_role_and_remove(ctx, owner):
    ctx.api("POST", "/users/invite", owner, {"email": "bob@example.com"})
    status, bob = ctx.api("PATCH", "/users/user-bob/role", owner, {"role": "admin"})
    assert status == 200 and bob["role"] == "admin"

    assert ctx.api("DELETE", "/users/user-bob", owner)[0] == 204
    _, users = ctx.api("GET", "/users", owner)
    assert len(users) == 1


def test_tenants_are_isolated(ctx, owner):
    ctx.api("POST", "/users/invite", owner, {"email": "bob@example.com"})
    _, other = ctx.api("POST", "/tenants", claims("zoe"), {"name": "Other"})
    zoe = claims("zoe", other["tenant_id"], "owner")

    _, users = ctx.api("GET", "/users", zoe)
    assert [u["email"] for u in users] == ["zoe@example.com"]
    assert ctx.api("DELETE", "/users/user-bob", zoe)[0] == 404


def test_swagger_lists_routes(ctx):
    status, _ = ctx.api("GET", "/swagger", query={"format": "json"})
    assert status == 200
