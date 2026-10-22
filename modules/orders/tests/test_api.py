from testkit.http import claims

ORDER = {"item": "Book", "quantity": 2, "price_cents": 1500}


def test_create_list_get(ctx, alice):
    status, order = ctx.api("POST", "/orders", alice, ORDER)
    assert status == 201 and order["tenant_id"] == "t1" and order["created_by"] == "alice"

    event = ctx.publisher.last()
    assert event["type"] == "OrderCreated" and event["detail"]["item"] == "Book"

    _, orders = ctx.api("GET", "/orders", alice)
    assert len(orders) == 1
    status, fetched = ctx.api("GET", f"/orders/{order['order_id']}", alice)
    assert status == 200 and fetched["item"] == "Book"


def test_tenants_cannot_see_each_others_orders(ctx, alice):
    _, order = ctx.api("POST", "/orders", alice, ORDER)
    zoe = claims("zoe", "t2", "owner")
    assert ctx.api("GET", f"/orders/{order['order_id']}", zoe)[0] == 404
    assert ctx.api("GET", "/orders", zoe)[1] == []


def test_status_rules(ctx, alice):
    _, order = ctx.api("POST", "/orders", alice, ORDER)
    path = f"/orders/{order['order_id']}/status"
    assert ctx.api("PATCH", path, alice, {"status": "shipped"})[0] == 400  # must be paid first
    status, updated = ctx.api("PATCH", path, alice, {"status": "paid"})
    assert status == 200 and updated["status"] == "paid"


def test_invalid_input(ctx, alice):
    assert ctx.api("POST", "/orders", alice, {**ORDER, "quantity": 0})[0] == 422
    assert ctx.api("GET", "/orders", alice, query={"limit": "500"})[0] == 422


def test_no_workspace_is_blocked(ctx):
    assert ctx.api("GET", "/orders", claims("newbie"))[0] == 403


def test_plan_limit_comes_from_token(ctx, alice):
    # Fill tenant t1 up to the Free limit (100 orders) directly in the table.
    with ctx.table.batch_writer() as batch:
        for i in range(100):
            batch.put_item(Item={"pk": "TENANT#t1", "sk": f"ORDER#{i}"})

    status, body = ctx.api("POST", "/orders", alice, ORDER)
    assert status == 402 and "Upgrade" in body["message"]

    # Same tenant, but the token says Pro: allowed.
    pro_alice = claims("alice", "t1", "member", plan="pro")
    assert ctx.api("POST", "/orders", pro_alice, ORDER)[0] == 201
