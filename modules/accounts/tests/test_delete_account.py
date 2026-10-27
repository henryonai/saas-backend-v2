"""Deleting an account: the user leaves every workspace, then their records and login go."""
from testkit.http import claims


def delete_account(ctx, user, email=None):
    return ctx.api("DELETE", "/account", user, {"email": email or f"{user['sub']}@example.com"}, tenant_id=None)


def test_a_member_deletes_their_account_leaving_every_workspace(ctx, owner, join):
    bob = join("bob")
    status, other = ctx.api("POST", "/tenants", claims("carol"), {"name": "Carol's"})
    ctx.api("POST", "/users/invite", claims("carol", other["tenant_id"], "owner"), {"email": "bob@example.com"})
    ctx.api("POST", f"/invitations/{other['tenant_id']}/accept", claims("bob"))

    assert delete_account(ctx, bob, email="BOB@example.com") == (204, None)   # case doesn't matter

    assert ctx.identity.deleted == ["bob"]
    assert ctx.login("bob") == {}
    assert [m["email"] for m in ctx.api("GET", "/users", owner)[1]] == ["alice@example.com"]
    assert ctx.api("GET", "/tenants/me", owner)[1]["seats_used"] == 1        # his seat is free again
    assert not [i for i in ctx.table.scan()["Items"] if i["pk"] == "USER#bob"]
    removed = [e["detail"]["tenant_id"] for e in ctx.events() if e["type"] == "MemberRemoved"]
    assert sorted(removed) == sorted([owner["tenant_id"], other["tenant_id"]])
    [deleted] = [e["detail"] for e in ctx.events() if e["type"] == "UserDeleted"]
    assert (deleted["user_id"], deleted["email"]) == ("bob", "bob@example.com")
    assert delete_account(ctx, bob)[0] == 204                              # again: nothing left, no error


def test_the_email_must_be_typed_to_confirm(ctx, owner, join):
    bob = join("bob")
    status, body = delete_account(ctx, bob, email="someone@example.com")
    assert status == 400 and "email" in body["message"]
    assert ctx.identity.deleted == []


def test_an_owner_deletes_their_workspaces_first(ctx, owner):
    status, body = delete_account(ctx, owner)
    assert status == 409 and "Acme" in body["message"]
    assert ctx.identity.deleted == []

    ctx.api("DELETE", "/tenants/me", owner, {"name": "Acme"})               # deleted: waits for the purge job
    assert delete_account(ctx, owner)[0] == 204
    assert ctx.identity.deleted == ["alice"]
    kept = sorted(i["sk"] for i in ctx.table.scan()["Items"] if i["pk"] == "USER#alice")
    assert kept == [f"WORKSPACE#{owner['tenant_id']}"]                      # the purge job removes it
    assert ctx.service.purge_due(2_000_000_000, lambda: 300) == [owner["tenant_id"]]
    assert not [i for i in ctx.table.scan()["Items"] if i["pk"] == "USER#alice"]   # nothing left behind
