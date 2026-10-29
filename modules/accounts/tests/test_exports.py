"""Exports: a job the user starts and watches; every module writes its part."""
from accounts.subscriptions import process_batch
from shared.exports import part_key
from testkit.http import FakeContext, claims
from testkit.sqs import sqs_event


def deliver(ctx, *messages):
    return process_batch(sqs_event(*messages), FakeContext(), ctx.service)


def object_created(key: str) -> tuple[str, dict]:
    """The exports bucket's event, as EventBridge puts it on the queue."""
    return "Object Created", {"bucket": {"name": "exports"}, "object": {"key": key, "size": 10}}


def requested(ctx) -> dict:
    [event] = [e["detail"] for e in ctx.events() if e["type"] == "ExportRequested"]
    return event


def test_an_owner_exports_the_workspace_and_downloads_it_once_every_module_wrote_its_part(ctx, owner, join):
    join("bob")
    status, job = ctx.api("POST", "/exports/workspace", owner)
    assert status == 202 and job["status"] == "running" and job["parts"] == ["accounts", "orders", "billing"]
    detail = requested(ctx)
    assert (detail["kind"], detail["owner_id"], detail["tenant_ids"]) == ("workspace", owner["tenant_id"],
                                                                           [owner["tenant_id"]])

    assert deliver(ctx, ("ExportRequested", detail)) == {"batchItemFailures": []}     # accounts' own part
    key = part_key("workspace", owner["tenant_id"], job["job_id"], "accounts")
    assert sorted(m["email"] for m in ctx.exports.written[key]["members"]) == ["alice@example.com",
                                                                               "bob@example.com"]
    deliver(ctx, object_created(key), object_created(part_key("workspace", owner["tenant_id"], job["job_id"],
                                                              "orders")))
    _, latest = ctx.api("GET", "/exports/workspace", owner)
    assert latest["status"] == "running" and sorted(latest["done"]) == ["accounts", "orders"]
    assert latest["files"] == {}

    billing = object_created(part_key("workspace", owner["tenant_id"], job["job_id"], "billing"))
    deliver(ctx, billing, billing)                                                    # delivered twice
    _, latest = ctx.api("GET", "/exports/workspace", owner)
    assert latest["status"] == "done" and set(latest["files"]) == {"accounts", "orders", "billing"}
    assert [e["type"] for e in ctx.events()].count("ExportReady") == 1


def test_only_the_owner_exports_a_workspace(ctx, owner, join):
    bob = join("bob", role="admin")
    assert ctx.api("POST", "/exports/workspace", bob)[0] == 403
    assert ctx.api("GET", "/exports/workspace", bob)[0] == 403


def test_a_user_exports_their_own_data_from_every_workspace(ctx, owner, join):
    bob = join("bob")
    status, job = ctx.api("POST", "/exports/me", bob, tenant_id=None)
    assert status == 202 and job["parts"] == ["accounts", "notifications", "assistant"]
    detail = requested(ctx)
    assert (detail["kind"], detail["owner_id"], detail["tenant_id"]) == ("user", "bob", "")
    assert detail["tenant_ids"] == [owner["tenant_id"]] and detail["email"] == "bob@example.com"

    deliver(ctx, ("ExportRequested", detail))
    mine = ctx.exports.written[part_key("user", "bob", job["job_id"], "accounts")]
    assert [(w["name"], w["role"]) for w in mine["workspaces"]] == [("Acme", "member")]
    assert ctx.api("GET", "/exports/me", claims("alice"), tenant_id=None)[1] is None   # someone else's: not shown


def test_exports_are_limited_per_day(ctx, owner):
    for _ in range(3):
        assert ctx.api("POST", "/exports/workspace", owner)[0] == 202
    status, body = ctx.api("POST", "/exports/workspace", owner)
    assert status == 429 and "3 exports a day" in body["message"]


def test_objects_that_are_not_an_exports_part_are_ignored(ctx):
    assert deliver(ctx, object_created("exports/user/bob/missing-job/accounts.json"),
                   object_created("something/else.txt")) == {"batchItemFailures": []}
