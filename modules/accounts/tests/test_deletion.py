"""Deleting a workspace: the owner deletes it, can restore it, and the purge job removes it."""
import time
from types import SimpleNamespace

import pytest

from accounts import purge
from testkit.http import FakeContext, claims

DAY = 24 * 3600


def delete(ctx, owner, name="Acme"):
    return ctx.api("DELETE", "/tenants/me", owner, {"name": name})


def run_purge(ctx, now: float, seconds_left: float = 300):
    """Runs the job as if it were `now`, with `seconds_left` of the Lambda's time."""
    return ctx.service.purge_due(int(now), lambda: seconds_left)


def items_of(ctx, tenant_id: str) -> list[str]:
    return sorted(i["sk"] for i in ctx.table.scan()["Items"] if i["pk"] == f"TENANT#{tenant_id}")


def test_the_owner_deletes_a_workspace_typing_its_name_and_it_leaves_every_members_token(ctx, owner, join):
    join("bob")
    status, body = delete(ctx, owner, name="acme")
    assert status == 400 and "exactly" in body["message"]            # the name, exactly, to confirm

    status, tenant = delete(ctx, owner)
    assert status == 200 and tenant["status"] == "deleting"
    assert tenant["delete_after"] >= time.time() + 7 * DAY - 5
    assert ctx.login("alice") == {} and ctx.login("bob") == {}       # their next tokens don't list it

    _, mine = ctx.api("GET", "/tenants", claims("alice"))            # listed with its status, to restore
    assert [(w["name"], w["status"]) for w in mine] == [("Acme", "deleting")]
    assert delete(ctx, owner)[0] == 409


def test_only_the_owner_deletes_or_restores_a_workspace(ctx, owner, join):
    admin = join("carol", role="admin")
    assert delete(ctx, admin)[0] == 403
    delete(ctx, owner)
    tenant_id = owner["tenant_id"]
    assert ctx.api("POST", f"/tenants/{tenant_id}/restore", claims("carol"))[0] == 403
    assert ctx.api("POST", f"/tenants/{tenant_id}/restore", claims("mallory"))[0] == 404


def test_the_owner_restores_a_deleted_workspace_before_it_is_purged(ctx, owner, join):
    join("bob")
    delete(ctx, owner)
    status, tenant = ctx.api("POST", f"/tenants/{owner['tenant_id']}/restore", claims("alice"))
    assert status == 200 and tenant["status"] == "active" and tenant["delete_after"] is None
    assert set(ctx.login("bob")) == {owner["tenant_id"]}
    assert ctx.api("POST", f"/tenants/{owner['tenant_id']}/restore", claims("alice"))[0] == 409
    assert run_purge(ctx, time.time() + 30 * DAY) == []               # no longer due


def test_a_deleted_workspace_takes_no_invitations(ctx, owner):
    assert ctx.api("POST", "/users/invite", owner, {"email": "dave@example.com"})[0] == 201
    delete(ctx, owner)
    assert ctx.api("POST", "/users/invite", owner, {"email": "erin@example.com"})[0] == 409
    assert ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims("dave"))[0] == 404


def test_the_purge_job_removes_a_workspace_once_its_grace_period_ends(ctx, owner, join):
    join("bob")
    ctx.api("POST", "/users/invite", owner, {"email": "dave@example.com"})
    status, other = ctx.api("POST", "/tenants", claims("bob"), {"name": "Bob's"})
    delete(ctx, owner)
    tenant_id = owner["tenant_id"]

    assert run_purge(ctx, time.time() + 6 * DAY) == []                # still within the grace period
    assert run_purge(ctx, time.time() + 7 * DAY + 60) == [tenant_id]

    assert items_of(ctx, tenant_id) == []                             # profile, members, invitations
    assert set(ctx.login("bob")) == {other["tenant_id"]}             # bob keeps his own workspace
    _, mine = ctx.api("GET", "/tenants", claims("alice"))
    assert mine == []
    counts = {u: ctx.table.get_item(Key={"pk": f"USER#{u}", "sk": "WORKSPACES"})["Item"]["count"]
              for u in ("alice", "bob")}
    assert counts == {"alice": 0, "bob": 1}                           # both can join others again

    [deleted] = [e for e in ctx.events() if e["type"] == "TenantDeleted"]
    assert deleted["detail"]["tenant_id"] == tenant_id and deleted["detail"]["name"] == "Acme"
    assert sorted(deleted["detail"]["user_ids"]) == ["alice", "bob"]
    assert run_purge(ctx, time.time() + 8 * DAY) == []                # nothing left to do


def test_a_purge_stopped_half_way_finishes_on_the_next_run_and_publishes_once(ctx, owner, join):
    join("bob")
    delete(ctx, owner)
    tenant_id = owner["tenant_id"]
    repo = ctx.service.accounts
    real_purge_rest = repo.purge_rest

    def crash(_tenant_id):
        raise TimeoutError("the run timed out")

    repo.purge_rest = crash
    with pytest.raises(TimeoutError):
        run_purge(ctx, time.time() + 8 * DAY)
    repo.purge_rest = real_purge_rest
    assert repo.get_tenant(tenant_id).status == "purging"
    assert ctx.api("POST", f"/tenants/{tenant_id}/restore", claims("alice"))[0] in (404, 409)   # too late

    assert run_purge(ctx, time.time() + 8 * DAY) == [tenant_id]
    assert items_of(ctx, tenant_id) == []
    assert [e["type"] for e in ctx.events()].count("TenantDeleted") == 1


def test_the_purge_job_stops_before_the_lambda_runs_out_of_time(ctx, owner):
    delete(ctx, owner)
    assert run_purge(ctx, time.time() + 8 * DAY, seconds_left=30) == []    # less than the margin: next run
    assert ctx.service.accounts.get_tenant(owner["tenant_id"]).status == "deleting"


def test_the_scheduled_handler_runs_the_purge_with_the_lambdas_remaining_time(ctx, owner):
    delete(ctx, owner)
    ctx.service.deletion_days = 0
    assert purge.run(FakeContext(), ctx.service) == {"purged": 0}             # not due for 7 days
    ctx.api("POST", f"/tenants/{owner['tenant_id']}/restore", claims("alice"))
    delete(ctx, owner)                                                        # deleted again, due now
    assert purge.run(SimpleNamespace(get_remaining_time_in_millis=lambda: 300_000), ctx.service) == {"purged": 1}
