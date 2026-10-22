import json

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


def test_cannot_create_second_workspace(ctx, owner):
    status, _ = ctx.api("POST", "/tenants", claims("alice"), {"name": "Another"})
    assert status == 409


def test_one_workspace_per_user_even_for_simultaneous_requests(ctx):
    # Both requests passed every check; the conditional write lets only one win.
    repo = ctx.service.accounts
    first, second = Tenant(name="First", owner_id="bob"), Tenant(name="Second", owner_id="bob")
    repo.create_tenant(first, Member(tenant_id=first.tenant_id, user_id="bob", email="b@x.com", role="owner"))
    with pytest.raises(ConditionFailed):
        repo.create_tenant(second, Member(tenant_id=second.tenant_id, user_id="bob", email="b@x.com", role="owner"))


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


def test_invited_person_chooses_to_accept(ctx, owner):
    invite(ctx, owner, "bob", role="admin")
    assert ctx.login("bob") == {}      # nothing happens just by signing in

    _, mine = ctx.api("GET", "/invitations", claims("bob"))
    assert [(i["tenant_name"], i["role"]) for i in mine] == [("Acme", "admin")]

    status, tenant = ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("bob"))
    assert status == 200 and tenant["name"] == "Acme"
    assert ctx.login("bob") == {"tenant_id": owner["tenant_id"], "role": "admin", "plan": "free"}
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


def test_member_of_another_workspace_must_leave_before_accepting(ctx, owner, join):
    _, other = ctx.api("POST", "/tenants", claims("zoe"), {"name": "Zoe Inc"})
    zoe_owner = claims("zoe", other["tenant_id"], "owner")
    invite(ctx, zoe_owner, "bob")
    bob = join("bob")

    status, body = ctx.api("POST", f"/invitations/{other['tenant_id']}/accept", claims("bob"))
    assert status == 409 and "Leave" in body["message"]
    assert ctx.api("DELETE", "/users/bob", bob)[0] == 204          # bob leaves Acme
    assert ctx.api("POST", f"/invitations/{other['tenant_id']}/accept", claims("bob"))[0] == 200
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
                                           invited_by="alice@example.com"),
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
    assert ctx.login("bob")["role"] == "admin"

    assert ctx.api("DELETE", "/users/bob", owner)[0] == 204
    assert ctx.identity.signed_out == ["bob", "bob"]
    assert len(ctx.api("GET", "/users", owner)[1]) == 1
    assert seats(ctx, owner) == 1
    assert ctx.login("bob") == {}


def test_demoted_admin_cannot_use_old_token_to_promote_themselves(ctx, owner, join):
    bob_old_token = join("bob", role="admin")
    ctx.api("PATCH", "/users/bob/role", owner, {"role": "member"})

    assert ctx.api("PATCH", "/users/bob/role", bob_old_token, {"role": "admin"})[0] == 403
    assert ctx.login("bob")["role"] == "member"


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
    ctx.service.accounts.remove_member(owner["tenant_id"], "bob")   # lands between check and write
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
