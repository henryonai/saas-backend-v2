import json
import time

import pytest

from accounts.handler import app
from accounts.models import Invitation, Member, Tenant
from shared.dynamo import ConditionFailed
from shared.events import Event
from testkit.http import claims


def seats(ctx, owner) -> int:
    return ctx.api("GET", "/tenants/me", owner)[1]["seats_used"]


def invite(ctx, owner, name: str, role: str = "member") -> int:
    return ctx.api("POST", "/users/invite", owner, {"email": f"{name}@example.com", "role": role})[0]


def test_signup_creates_workspace_with_owner(ctx, owner):
    status, tenant = ctx.api("GET", "/tenants/me", owner)
    assert status == 200 and tenant["name"] == "Acme" and tenant["plan"] == "free"
    assert tenant["seats_used"] == 1

    _, users = ctx.api("GET", "/users", owner)
    assert [(u["email"], u["role"]) for u in users] == [("alice@example.com", "owner")]


def test_api_does_not_expose_internal_tenant_fields(ctx, owner):
    ctx.service.apply_subscription_change(owner["tenant_id"], "pro", "active", 3)
    _, tenant = ctx.api("GET", "/tenants/me", owner)
    assert tenant["plan"] == "pro" and "subscription_revision" not in tenant
    stored = ctx.service.accounts.get_tenant(owner["tenant_id"])
    assert stored.subscription_revision == 3   # still stored, just not part of the API


def test_a_user_can_create_several_workspaces_up_to_the_limit(ctx, owner):
    status, beta = ctx.api("POST", "/tenants", claims("alice"), {"name": "beta"})
    assert status == 201
    assert set(ctx.login("alice")) == {owner["tenant_id"], beta["tenant_id"]}

    _, mine = ctx.api("GET", "/tenants", claims("alice"))      # no X-Tenant-Id needed
    assert [(w["name"], w["role"], w["plan"]) for w in mine] == [("Acme", "owner", "free"), ("beta", "owner", "free")]

    assert ctx.api("POST", "/tenants", claims("alice"), {"name": "Gamma"})[0] == 201   # 3 of MAX_WORKSPACES
    status, body = ctx.api("POST", "/tenants", claims("alice"), {"name": "Delta"})
    assert status == 409 and "at most 3" in body["message"]


def test_the_workspace_limit_holds_for_simultaneous_requests(ctx):
    # Both requests passed every check; the conditional write lets only one win.
    repo = ctx.service.accounts
    first, second = Tenant(name="First", owner_id="bob"), Tenant(name="Second", owner_id="bob")
    repo.create_tenant(first, Member(tenant_id=first.tenant_id, user_id="bob", email="b@x.com", role="owner"), 1,
                       idempotency=("key-1", "first"))
    with pytest.raises(ConditionFailed) as error:
        repo.create_tenant(second, Member(tenant_id=second.tenant_id, user_id="bob", email="b@x.com", role="owner"),
                           1, idempotency=("key-2", "second"))
    assert error.value.failed == {"workspaces"}
    assert list(ctx.login("bob")) == [first.tenant_id]
    assert "Item" not in ctx.table.get_item(Key={"pk": "USER#bob", "sk": "IDEMPOTENCY#bob#key-2"})   # nor its key


def test_a_retry_with_the_same_idempotency_key_returns_the_first_workspace(ctx, owner):
    key = {"Idempotency-Key": "4f9a1c2e-7b3d-4e8f-9a6b-0c1d2e3f4a5b"}
    status, beta = ctx.api("POST", "/tenants", claims("alice"), {"name": "Beta"}, headers=key)
    assert status == 201
    assert ctx.api("POST", "/tenants", claims("alice"), {"name": "Gamma"})[0] == 201   # now at MAX_WORKSPACES

    # The first request was counted: its retry returns it, not "too many workspaces".
    assert ctx.api("POST", "/tenants", claims("alice"), {"name": "Beta"}, headers=key) == (201, beta)
    assert sorted(w["name"] for w in ctx.api("GET", "/tenants", claims("alice"))[1]) == ["Acme", "Beta", "Gamma"]
    count = ctx.table.get_item(Key={"pk": "USER#alice", "sk": "WORKSPACES"})["Item"]["count"]
    assert count == 3


def test_an_idempotency_key_used_for_another_workspace_is_refused(ctx, owner):
    key = {"Idempotency-Key": "key-1"}
    ctx.api("POST", "/tenants", claims("alice"), {"name": "Beta"}, headers=key)
    status, body = ctx.api("POST", "/tenants", claims("alice"), {"name": "Gamma"}, headers=key)
    assert status == 400 and "different request" in body["message"]
    assert len(ctx.api("GET", "/tenants", claims("alice"))[1]) == 2


def test_a_workspace_without_an_idempotency_key_is_refused(ctx):
    """Without one, a retried create could make a second workspace."""
    status, body = ctx.api("POST", "/tenants", claims("bob"), {"name": "Bob's shop"}, headers={})
    assert status == 422 and "idempotency-key" in json.dumps(body).lower()
    assert ctx.api("GET", "/tenants", claims("bob"))[1] == []


def test_keys_are_per_user(ctx):
    key = {"Idempotency-Key": "key-1"}
    _, bob = ctx.api("POST", "/tenants", claims("bob"), {"name": "Shop"}, headers=key)
    _, carol = ctx.api("POST", "/tenants", claims("carol"), {"name": "Shop"}, headers=key)
    assert bob["tenant_id"] != carol["tenant_id"]


def test_no_workspace_yet_is_blocked(ctx):
    status, body = ctx.api("GET", "/users", claims("bob"))
    assert status == 403 and "workspace" in body["message"]


def test_invite_creates_pending_invitation_and_fat_event(ctx, owner):
    status, invitation = ctx.api("POST", "/users/invite", owner, {"email": "Bob@Example.com", "role": "admin"})
    assert status == 201 and invitation["email"] == "bob@example.com" and invitation["role"] == "admin"
    assert seats(ctx, owner) == 1     # a seat is taken only when accepted

    [event] = ctx.events()
    assert event["type"] == "UserInvited"
    assert event["detail"]["tenant_name"] == "Acme"
    assert event["detail"]["invited_by"] == "alice@example.com"

    _, pending = ctx.api("GET", "/users/invitations", owner)
    assert [i["email"] for i in pending] == ["bob@example.com"]


def test_invitation_expires_after_the_configured_days(ctx, owner):
    _, invitation = ctx.api("POST", "/users/invite", owner, {"email": "bob@example.com"})
    assert abs(invitation["expires_at"] - (time.time() + 7 * 24 * 3600)) < 60


def test_invited_person_chooses_to_accept(ctx, owner):
    invite(ctx, owner, "bob", role="admin")
    assert ctx.login("bob") == {}      # nothing happens just by signing in

    _, mine = ctx.api("GET", "/invitations", claims("bob"))
    assert [(i["tenant_name"], i["role"]) for i in mine] == [("Acme", "admin")]

    status, tenant = ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("bob"))
    assert status == 200 and tenant["name"] == "Acme"
    assert ctx.login("bob") == {owner["tenant_id"]: {"role": "admin", "plan": "free"}}
    assert ctx.api("GET", "/users/invitations", owner)[1] == []
    assert seats(ctx, owner) == 2


def test_nobody_is_pulled_into_a_workspace_they_did_not_accept(ctx):
    # A stranger invites the victim before the victim signs up.
    _, other = ctx.api("POST", "/tenants", claims("mallory"), {"name": "Victim Corp"})
    mallory = claims("mallory", other["tenant_id"], "owner")
    invite(ctx, mallory, "victim")

    assert ctx.login("victim") == {}
    assert ctx.api("POST", "/tenants", claims("victim"), {"name": "My company"})[0] == 201
    assert ctx.api("POST", f"/invitations/{other['tenant_id']}/decline", claims("victim"))[0] == 204


def test_accepting_needs_a_verified_email_and_a_matching_invitation(ctx, owner):
    invite(ctx, owner, "bob")
    unverified = claims("bob", email_verified=False)
    assert ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", unverified)[0] == 403
    assert ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("carol"))[0] == 404


def test_expired_invitation_cannot_be_accepted(ctx, owner):
    invite(ctx, owner, "bob")
    ctx.table.update_item(Key={"pk": f"TENANT#{owner['tenant_id']}", "sk": "INVITE#bob@example.com"},
                          UpdateExpression="SET expires_at = :past", ExpressionAttributeValues={":past": 1})
    assert ctx.api("GET", "/invitations", claims("bob"))[1] == []
    assert ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("bob"))[0] == 404
    assert invite(ctx, owner, "bob") == 201   # can be invited again


def test_a_member_of_one_workspace_can_join_another(ctx, owner, join):
    _, other = ctx.api("POST", "/tenants", claims("zoe"), {"name": "Zoe Inc"})
    invite(ctx, claims("zoe", other["tenant_id"], "owner"), "bob")
    join("bob")

    assert ctx.api("POST", f"/invitations/{other['tenant_id']}/accept", claims("bob"))[0] == 200
    assert ctx.login("bob") == {owner["tenant_id"]: {"role": "member", "plan": "free"},
                                other["tenant_id"]: {"role": "member", "plan": "free"}}
    assert seats(ctx, owner) == 2                                   # still in Acme too


def test_accepting_an_invitation_to_a_workspace_you_are_in_is_refused(ctx, owner, join):
    join("bob")
    # An invitation that slipped in anyway (the invite check and the accept race).
    ctx.service.accounts.invite(Invitation(tenant_id=owner["tenant_id"], tenant_name="Acme",
                                           email="bob@example.com", role="admin",
                                           invited_by="alice@example.com", expires_at=int(time.time()) + 3600),
                                event=Event("UserInvited", owner["tenant_id"]), invites_per_day=100)
    status, body = ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("bob"))
    assert status == 409 and "already in this workspace" in body["message"]
    assert ctx.login("bob")[owner["tenant_id"]]["role"] == "member"   # unchanged
    assert seats(ctx, owner) == 2


def test_each_request_acts_in_the_workspace_it_names(ctx, owner, join):
    _, other = ctx.api("POST", "/tenants", claims("zoe"), {"name": "Zoe Inc"})
    invite(ctx, claims("zoe", other["tenant_id"], "owner"), "bob")
    join("bob")
    ctx.api("POST", f"/invitations/{other['tenant_id']}/accept", claims("bob"))
    bob = claims("bob", owner["tenant_id"], "member", workspaces={other["tenant_id"]: ("member", "free")})

    in_acme = ctx.api("GET", "/users", bob, tenant_id=owner["tenant_id"])[1]
    in_zoe = ctx.api("GET", "/users", bob, tenant_id=other["tenant_id"])[1]
    assert sorted(u["email"] for u in in_acme) == ["alice@example.com", "bob@example.com"]
    assert sorted(u["email"] for u in in_zoe) == ["bob@example.com", "zoe@example.com"]
    assert ctx.api("GET", "/users", bob)[0] == 403                  # two workspaces, none named
    assert ctx.api("GET", "/users", claims("bob", owner["tenant_id"], "member"),
                   tenant_id=other["tenant_id"])[0] == 401          # an older token that doesn't list Zoe Inc


def test_leaving_keeps_the_session_and_the_other_workspaces(ctx, owner, join):
    _, other = ctx.api("POST", "/tenants", claims("bob"), {"name": "Bob's shop"})
    bob = join("bob")

    assert ctx.api("DELETE", "/users/bob", bob)[0] == 204           # bob leaves Acme
    assert ctx.identity.signed_out == []                            # still signed in
    assert list(ctx.login("bob")) == [other["tenant_id"]]           # his next token drops Acme
    assert [w["name"] for w in ctx.api("GET", "/tenants", claims("bob"))[1]] == ["Bob's shop"]
    assert seats(ctx, owner) == 1


def test_duplicate_invite_and_existing_member_rejected(ctx, owner, join):
    invite(ctx, owner, "bob")
    assert invite(ctx, owner, "bob") == 409
    join("carol")
    assert invite(ctx, owner, "carol") == 409


def test_member_cannot_invite(ctx, owner, join):
    carol = join("carol")
    assert ctx.api("POST", "/users/invite", carol, {"email": "dave@example.com"})[0] == 403


def test_unverified_email_cannot_invite(ctx, owner):
    unverified = {**owner, "email_verified": "false"}
    status, body = ctx.api("POST", "/users/invite", unverified, {"email": "dave@example.com"})
    assert status == 403 and "Verify" in body["message"]


def test_free_plan_limits_invitations_and_seats(ctx, owner):
    for name in ("bob", "carol"):  # owner + 2 = Free limit of 3
        assert invite(ctx, owner, name) == 201
    status, body = ctx.api("POST", "/users/invite", owner, {"email": "dave@example.com"})
    assert status == 402 and "Upgrade" in body["message"]

    assert ctx.api("DELETE", "/users/invitations/carol%40example.com", owner)[0] == 204
    assert invite(ctx, owner, "dave") == 201
    assert ctx.api("DELETE", "/users/invitations/nobody@example.com", owner)[0] == 404


def test_seat_limit_is_enforced_when_accepting(ctx, owner, join):
    join("bob")
    join("carol")                     # 3 of 3 seats
    # An invitation that slipped in anyway (e.g. sent before a downgrade) can't overfill.
    ctx.service.accounts.invite(Invitation(tenant_id=owner["tenant_id"], tenant_name="Acme",
                                           email="dave@example.com", role="member",
                                           invited_by="alice@example.com", expires_at=int(time.time()) + 3600),
                                event=Event("UserInvited", owner["tenant_id"]),
                                invites_per_day=100)
    status, body = ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("dave"))
    assert status == 402 and "full" in body["message"]


def test_daily_invite_limit(ctx, owner):
    ctx.service.apply_subscription_change(owner["tenant_id"], "pro", "active", 1)  # plenty of seats
    for i in range(5):
        assert invite(ctx, owner, f"u{i}") == 201
        ctx.api("DELETE", f"/users/invitations/u{i}@example.com", owner)  # cancelling doesn't refund
    status, body = ctx.api("POST", "/users/invite", owner, {"email": "u9@example.com"})
    assert status == 429 and "a day" in body["message"]


def test_one_email_cannot_be_flooded_with_invitations(ctx):
    for i in range(5):
        _, tenant = ctx.api("POST", "/tenants", claims(f"spammer{i}"), {"name": f"Spam {i}"})
        assert invite(ctx, claims(f"spammer{i}", tenant["tenant_id"], "owner"), "victim") == 201
    _, tenant = ctx.api("POST", "/tenants", claims("spammer9"), {"name": "Spam 9"})
    assert invite(ctx, claims("spammer9", tenant["tenant_id"], "owner"), "victim") == 429


def test_invalid_email_rejected(ctx, owner):
    status, _ = ctx.api("POST", "/users/invite", owner, {"email": "not-an-email"})
    assert status == 422


def test_owner_is_protected(ctx, owner):
    assert ctx.api("PATCH", "/users/alice/role", owner, {"role": "member"})[0] == 403
    assert ctx.api("DELETE", "/users/alice", owner)[0] == 403   # can't leave either


def test_change_role_and_remove_sign_the_user_out(ctx, owner, join):
    join("bob")
    status, bob = ctx.api("PATCH", "/users/bob/role", owner, {"role": "admin"})
    assert status == 200 and bob["role"] == "admin"
    assert ctx.login("bob")[owner["tenant_id"]]["role"] == "admin"

    assert ctx.api("DELETE", "/users/bob", owner)[0] == 204
    assert ctx.identity.signed_out == ["bob", "bob"]               # removed by someone else: signed out
    assert len(ctx.api("GET", "/users", owner)[1]) == 1
    assert seats(ctx, owner) == 1
    assert ctx.login("bob") == {}


def test_removing_a_member_or_leaving_publishes_member_removed(ctx, owner, join):
    join("bob")
    carol = join("carol")

    assert ctx.api("DELETE", "/users/bob", owner)[0] == 204
    assert ctx.api("DELETE", "/users/carol", carol)[0] == 204   # leaves
    assert ctx.api("DELETE", "/users/bob", owner)[0] == 404     # gone already: no second event
    assert ctx.identity.signed_out == ["bob"]                   # removed; carol left, so keeps her session

    removed = [e["detail"] for e in ctx.events() if e["type"] == "MemberRemoved"]
    assert sorted((d["user_id"], d["removed_by"]) for d in removed) == [("bob", "alice"), ("carol", "carol")]
    assert {d["tenant_id"] for d in removed} == {owner["tenant_id"]}


def test_demoted_admin_cannot_use_old_token_to_promote_themselves(ctx, owner, join):
    bob_old_token = join("bob", role="admin")
    ctx.api("PATCH", "/users/bob/role", owner, {"role": "member"})

    assert ctx.api("PATCH", "/users/bob/role", bob_old_token, {"role": "admin"})[0] == 403
    assert ctx.login("bob")[owner["tenant_id"]]["role"] == "member"


def test_removed_admin_cannot_use_old_token(ctx, owner, join):
    bob_old_token = join("bob", role="admin")
    ctx.api("DELETE", "/users/bob", owner)

    assert ctx.api("POST", "/users/invite", bob_old_token, {"email": "bob2@example.com"})[0] == 403
    assert ctx.api("GET", "/users", bob_old_token)[0] == 403


def test_removed_member_can_be_invited_again(ctx, owner, join):
    join("bob")
    ctx.api("DELETE", "/users/bob", owner)
    assert invite(ctx, owner, "bob") == 201
    assert ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("bob"))[0] == 200


def test_role_change_after_removal_does_not_create_a_broken_member(ctx, owner, join):
    join("bob")
    ctx.service.accounts.remove_member(owner["tenant_id"], "bob",   # lands between check and write
                                       Event("MemberRemoved", owner["tenant_id"]))
    assert not ctx.service.accounts.change_role(owner["tenant_id"], "bob", "admin")
    assert ctx.api("GET", "/users", owner)[0] == 200


def test_tenants_are_isolated(ctx, owner, join):
    join("bob")
    _, other = ctx.api("POST", "/tenants", claims("zoe"), {"name": "Other"})
    zoe = claims("zoe", other["tenant_id"], "owner")

    _, users = ctx.api("GET", "/users", zoe)
    assert [u["email"] for u in users] == ["zoe@example.com"]
    assert ctx.api("DELETE", "/users/bob", zoe)[0] == 404


def test_openapi_lists_routes():
    paths = json.loads(app.get_openapi_json_schema())["paths"]
    assert "/users/invite" in paths and "/invitations/{tenant_id}/accept" in paths
