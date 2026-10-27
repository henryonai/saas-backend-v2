from testkit.http import claims
from testkit.sqs import with_event_ids


def created(tenant_id="t1", name="Acme") -> tuple[str, dict]:
    return "TenantCreated", with_event_ids({"tenant_id": tenant_id, "name": name, "actor_id": "alice",
                                            "actor_email": "alice@example.com"})


def workspace(ctx, ops, tenant_id="t1") -> dict:
    return ctx.api("GET", f"/admin/tenants/{tenant_id}", ops, tenant_id=None)[1]


def test_the_summary_of_each_workspace_follows_its_events_once_each(ctx, ops):
    joined = ("MemberJoined", with_event_ids({"tenant_id": "t1", "user_id": "bob", "role": "member"}))
    order = ("OrderCreated", with_event_ids({"tenant_id": "t1", "user_id": "bob", "item": "Book"}))
    newer = ("SubscriptionChanged", with_event_ids({"tenant_id": "t1", "plan": "business", "status": "active",
                                                    "revision": 3}))
    older = ("SubscriptionChanged", with_event_ids({"tenant_id": "t1", "plan": "pro", "status": "active",
                                                    "revision": 2}))
    assert ctx.deliver(joined, created(), joined, order, newer, older) == {"batchItemFailures": []}

    summary = workspace(ctx, ops)
    assert (summary["name"], summary["owner_id"], summary["members"], summary["orders"]) == ("Acme", "alice", 2, 1)
    assert (summary["plan"], summary["status"]) == ("business", "active")     # the late revision 2 is skipped

    ctx.deliver(("TenantDeletionScheduled", with_event_ids({"tenant_id": "t1"})))
    assert workspace(ctx, ops)["status"] == "deleting"
    ctx.deliver(("TenantDeleted", with_event_ids({"tenant_id": "t1", "user_ids": ["alice"]})))
    assert ctx.api("GET", "/admin/tenants/t1", ops, tenant_id=None)[0] == 404


def test_only_platform_admins_use_the_back_office(ctx, ops):
    ctx.deliver(created())
    owner = claims("alice", "t1", "owner")
    for method, path, body in (("GET", "/admin/tenants", None), ("GET", "/admin/tenants/t1", None),
                               ("POST", "/admin/tenants/t1/plan", {"plan": "pro"}),
                               ("POST", "/admin/tenants/t1/suspend", {"reason": "x"}), ("GET", "/admin/log", None)):
        assert ctx.api(method, path, owner, body, tenant_id=None)[0] == 403
    status, page = ctx.api("GET", "/admin/tenants", ops, tenant_id=None)
    assert status == 200 and [w["name"] for w in page["items"]] == ["Acme"]


def test_an_admin_grants_a_plan_suspends_and_unsuspends_and_each_is_logged(ctx, ops):
    ctx.deliver(created())
    assert ctx.api("POST", "/admin/tenants/t1/plan", ops, {"plan": "gold"}, tenant_id=None)[0] == 400
    assert ctx.api("POST", "/admin/tenants/t1/plan", ops, {"plan": "pro", "note": "partner"},
                   tenant_id=None)[0] == 202
    assert ctx.api("POST", "/admin/tenants/t1/suspend", ops, {"reason": "spam"}, tenant_id=None)[0] == 202
    assert ctx.api("POST", "/admin/tenants/nope/suspend", ops, {"reason": "spam"}, tenant_id=None)[0] == 404

    published = [(e["type"], e["detail"]["tenant_id"], e["detail"]["actor_id"]) for e in ctx.events()]
    assert published == [("PlanGranted", "t1", "ops"), ("TenantSuspended", "t1", "ops")]
    ctx.deliver(*[(e["type"], e["detail"]) for e in ctx.events()])          # they come back as events
    assert workspace(ctx, ops)["status"] == "suspended"
    assert ctx.api("POST", "/admin/tenants/t1/suspend", ops, {"reason": "again"}, tenant_id=None)[0] == 409
    assert ctx.api("POST", "/admin/tenants/t1/unsuspend", ops, tenant_id=None)[0] == 202

    _, log = ctx.api("GET", "/admin/log", ops, tenant_id=None)
    assert [(a["action"], a["admin_email"]) for a in log["items"]] == [
        ("unsuspend", "ops@example.com"), ("suspend", "ops@example.com"), ("grant_plan", "ops@example.com")]
    assert log["items"][2]["details"] == {"plan": "pro", "note": "partner"}
