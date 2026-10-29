from shared.exports import part_key
from testkit.http import claims
from testkit.sqs import with_event_ids

OWNER = claims("alice", "t1", "owner")


def event(tenant_id="t1", at="2026-10-28T10:00:00+00:00", **detail) -> dict:
    return {**with_event_ids({"tenant_id": tenant_id, **detail}), "occurred_at": at}


def test_owners_and_admins_see_who_did_what_newest_first(ctx):
    joined = event(user_id="bob", role="member", actor_id="bob", actor_email="bob@example.com",
                   at="2026-10-28T10:00:00+00:00")
    promoted = event(user_id="bob", email="bob@example.com", role="admin", actor_id="alice",
                     actor_email="alice@example.com", at="2026-10-28T11:00:00+00:00")
    assert ctx.deliver(("RoleChanged", promoted), ("MemberJoined", joined), ("RoleChanged", promoted)) == \
        {"batchItemFailures": []}                                   # out of order, and one twice

    status, page = ctx.api("GET", "/audit", OWNER)
    assert status == 200
    assert [(e["type"], e["actor_email"]) for e in page["items"]] == [("RoleChanged", "alice@example.com"),
                                                                       ("MemberJoined", "bob@example.com")]
    assert page["items"][0]["details"] == {"user_id": "bob", "email": "bob@example.com", "role": "admin"}
    assert ctx.api("GET", "/audit", claims("carol", "t1", "admin"))[0] == 200
    assert ctx.api("GET", "/audit", claims("dave", "t1", "member"))[0] == 403


def test_a_workspace_sees_only_its_own_log(ctx):
    ctx.deliver(("OrderCreated", event(tenant_id="t2", user_id="mallory", item="Book")))
    assert ctx.api("GET", "/audit", OWNER)[1]["items"] == []


def test_the_actor_of_events_without_actor_id(ctx):
    ctx.deliver(("OrderCreated", event(user_id="bob", item="Book")),
                ("SubscriptionChanged", event(plan="pro", status="active", revision=2, owner_email="a@x.com")))
    items = ctx.api("GET", "/audit", OWNER)[1]["items"]
    assert {e["type"]: e["actor_id"] for e in items} == {"OrderCreated": "bob", "SubscriptionChanged": ""}
    assert "owner_email" not in next(e for e in items if e["type"] == "SubscriptionChanged")["details"]


def test_pages(ctx):
    for minute in range(5):
        ctx.deliver(("OrderCreated", event(user_id="bob", item=str(minute), at=f"2026-10-28T10:0{minute}:00+00:00")))
    first = ctx.api("GET", "/audit", OWNER, query={"limit": "2"})[1]
    second = ctx.api("GET", "/audit", OWNER, query={"limit": "2", "cursor": first["next_cursor"]})[1]
    assert [e["details"]["item"] for e in first["items"] + second["items"]] == ["4", "3", "2", "1"]
    assert ctx.api("GET", "/audit", OWNER, query={"cursor": "not-a-cursor"})[0] == 400


def test_a_workspace_export_has_its_log_and_a_purged_workspace_loses_it(ctx):
    ctx.deliver(("OrderCreated", event(user_id="bob", item="Book")))
    requested = event(job_id="job-1", kind="workspace", owner_id="t1", user_id="alice", email="alice@example.com",
                      tenant_ids=["t1"])
    ctx.deliver(("ExportRequested", requested))
    assert [e["type"] for e in ctx.exports.written[part_key("workspace", "t1", "job-1", "audit")]] == ["OrderCreated"]

    ctx.deliver(("TenantDeleted", event(user_ids=["alice"])))
    assert ctx.table.scan()["Items"] == []
