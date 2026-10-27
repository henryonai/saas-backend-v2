from orders.listener import process_batch
from testkit.http import FakeContext, claims
from testkit.sqs import sqs_event, with_event_ids

ORDER = {"item": "Book", "quantity": 2, "price_cents": 1500}


def test_a_purged_workspace_loses_its_orders_and_no_other_workspace_does(ctx, alice):
    bob = claims("bob", "t2", "member")
    for user in (alice, alice, bob):
        assert ctx.api("POST", "/orders", user, ORDER)[0] == 201

    deleted = ("TenantDeleted", with_event_ids({"tenant_id": "t1", "user_ids": ["alice"]}))
    assert process_batch(sqs_event(deleted, deleted), FakeContext(), ctx.service) == {"batchItemFailures": []}

    assert {i["pk"] for i in ctx.table.scan()["Items"] if i["pk"].startswith("TENANT#")} == {"TENANT#t2"}
    assert ctx.api("GET", "/orders", alice)[1]["items"] == []
    assert len(ctx.api("GET", "/orders", bob)[1]["items"]) == 1


def test_events_orders_does_not_handle_are_ignored(ctx):
    event = sqs_event(("SomethingElse", with_event_ids({"tenant_id": "t1"})))
    assert process_batch(event, FakeContext(), ctx.service) == {"batchItemFailures": []}


def export_requested(kind: str, owner_id: str) -> dict:
    return with_event_ids({"tenant_id": owner_id if kind == "workspace" else "", "job_id": "job-1", "kind": kind,
                           "owner_id": owner_id, "user_id": "alice", "email": "alice@example.com",
                           "tenant_ids": ["t1"]})


def test_a_workspace_export_gets_every_order_and_a_users_export_none(ctx, alice):
    from shared.exports import FakePartWriter, part_key
    ctx.service.exports = FakePartWriter()
    for _ in range(3):
        ctx.api("POST", "/orders", alice, ORDER)
    process_batch(sqs_event(("ExportRequested", export_requested("workspace", "t1")),
                            ("ExportRequested", export_requested("user", "alice"))), FakeContext(), ctx.service)
    assert list(ctx.service.exports.written) == [part_key("workspace", "t1", "job-1", "orders")]
    orders = ctx.service.exports.written[part_key("workspace", "t1", "job-1", "orders")]
    assert len(orders) == 3 and orders[0]["item"] == "Book"
