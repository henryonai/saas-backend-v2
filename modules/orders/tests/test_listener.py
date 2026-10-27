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
