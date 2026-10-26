import pytest

from orders.models import OrderStatus
from orders.repository import OrderRepository
from shared.dynamo import ConditionFailed
from shared.events import Event
from testkit.http import claims

ORDER = {"item": "Book", "quantity": 2, "price_cents": 1500}


def test_create_list_get(ctx, alice):
    status, order = ctx.api("POST", "/orders", alice, ORDER)
    assert status == 201 and order["tenant_id"] == "t1" and order["created_by"] == "alice"

    [event] = ctx.events()  # saved with the order, published later by the outbox relay
    assert event["type"] == "OrderCreated" and event["detail"]["item"] == "Book"
    assert event["detail"]["event_id"] and event["detail"]["tenant_id"] == "t1"

    _, page = ctx.api("GET", "/orders", alice)
    assert len(page["items"]) == 1 and page["next_cursor"] is None
    status, fetched = ctx.api("GET", f"/orders/{order['order_id']}", alice)
    assert status == 200 and fetched["item"] == "Book"


def test_tenants_cannot_see_each_others_orders(ctx, alice):
    _, order = ctx.api("POST", "/orders", alice, ORDER)
    zoe = claims("zoe", "t2", "owner")
    assert ctx.api("GET", f"/orders/{order['order_id']}", zoe)[0] == 404
    assert ctx.api("GET", "/orders", zoe)[1]["items"] == []


def test_list_is_newest_first_with_a_cursor(ctx, alice):
    created = [ctx.api("POST", "/orders", alice, {**ORDER, "item": f"item-{i}"})[1]["order_id"]
               for i in range(5)]
    _, first = ctx.api("GET", "/orders", alice, query={"limit": "3"})
    _, second = ctx.api("GET", "/orders", alice, query={"limit": "3", "cursor": first["next_cursor"]})

    listed = [o["order_id"] for o in first["items"] + second["items"]]
    assert listed == list(reversed(created))
    assert second["next_cursor"] is None


def test_status_rules(ctx, alice):
    _, order = ctx.api("POST", "/orders", alice, ORDER)
    path = f"/orders/{order['order_id']}/status"
    assert ctx.api("PATCH", path, alice, {"status": "shipped"})[0] == 400  # must be paid first
    status, updated = ctx.api("PATCH", path, alice, {"status": "paid"})
    assert status == 200 and updated["status"] == "paid"


def test_status_change_based_on_a_stale_read_is_rejected(ctx, alice):
    _, order = ctx.api("POST", "/orders", alice, ORDER)
    repo = OrderRepository(ctx.table)
    repo.update_status("t1", order["order_id"], OrderStatus.CANCELLED, expected=OrderStatus.PENDING)

    # A second request that still believes the order is pending must not win.
    assert not repo.update_status("t1", order["order_id"], OrderStatus.PAID, expected=OrderStatus.PENDING)
    assert repo.get("t1", order["order_id"]).status == OrderStatus.CANCELLED


def test_invalid_input(ctx, alice):
    assert ctx.api("POST", "/orders", alice, {**ORDER, "quantity": 0})[0] == 422
    assert ctx.api("GET", "/orders", alice, query={"limit": "500"})[0] == 422


def test_no_workspace_is_blocked(ctx):
    assert ctx.api("GET", "/orders", claims("newbie"))[0] == 403


def test_access_tokens_are_rejected(ctx, alice):
    access_token = {**alice, "token_use": "access"}
    assert ctx.api("GET", "/orders", access_token)[0] == 401


def test_workspaces_claims_we_did_not_write_are_refused(ctx, alice):
    assert ctx.api("GET", "/orders", {**alice, "workspaces": "not json"})[0] == 401
    superuser = {**alice, "workspaces": '{"t1": {"role": "superuser", "plan": "free"}}'}
    assert ctx.api("GET", "/orders", superuser, tenant_id="t1")[0] == 401


def test_orders_are_listed_for_the_workspace_the_request_names(ctx, alice):
    assert ctx.api("POST", "/orders", alice, ORDER)[0] == 201
    in_two = claims("alice", "t1", "member", workspaces={"t2": ("owner", "free")})
    assert len(ctx.api("GET", "/orders", in_two, tenant_id="t1")[1]["items"]) == 1
    assert ctx.api("GET", "/orders", in_two, tenant_id="t2")[1]["items"] == []
    assert ctx.api("GET", "/orders", alice, tenant_id="t2")[0] == 401   # a workspace alice's token doesn't list


def test_plan_limit_comes_from_token(ctx, alice):
    # Tenant t1 is at the Free limit (100 orders).
    ctx.table.put_item(Item={"pk": "TENANT#t1", "sk": "COUNTER", "order_count": 100})

    status, body = ctx.api("POST", "/orders", alice, ORDER)
    assert status == 402 and "Upgrade" in body["message"]
    assert ctx.events() == []  # nothing saved, nothing published

    # Same tenant, but the token says Pro: allowed.
    pro_alice = claims("alice", "t1", "member", plan="pro")
    assert ctx.api("POST", "/orders", pro_alice, ORDER)[0] == 201


def test_limit_is_checked_in_the_same_transaction_as_the_save(ctx, alice):
    ctx.table.put_item(Item={"pk": "TENANT#t1", "sk": "COUNTER", "order_count": 99})
    assert ctx.api("POST", "/orders", alice, ORDER)[0] == 201   # the 100th order
    assert ctx.api("POST", "/orders", alice, ORDER)[0] == 402   # no "count, then write" gap


def test_order_and_event_are_saved_together_or_not_at_all(ctx, alice):
    repo = OrderRepository(ctx.table)
    ctx.table.put_item(Item={"pk": "TENANT#t1", "sk": "COUNTER", "order_count": 100})
    from orders.models import Order
    order = Order(**ORDER, tenant_id="t1", created_by="alice")
    with pytest.raises(ConditionFailed):
        repo.create(order, Event("OrderCreated", "t1"), max_orders=100)
    assert repo.get("t1", order.order_id) is None and ctx.events() == []
