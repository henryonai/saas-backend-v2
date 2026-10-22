"""
Orders table:
  pk = TENANT#<tenant_id>   sk = ORDER#<order_id>   order (ids sort by creation time)
  pk = TENANT#<tenant_id>   sk = COUNTER            order_count, for the plan limit
  pk = OUTBOX#<event_id>    sk = OUTBOX             events to publish (shared/outbox.py)

The tenant is part of every key, so one tenant can never read another's orders.
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure, transact
from shared.events import Event
from shared.outbox import outbox_put

from orders.models import Order, OrderStatus


def _pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


class OrderRepository:
    def __init__(self, table):
        self.table = table

    def create(self, order: Order, event: Event, max_orders: int | None) -> None:
        """
        Saves the order and its event, and counts it against the plan limit,
        all in one transaction. Raises ConditionFailed({"limit"}) when the
        tenant already has max_orders orders (None = unlimited).
        """
        counter = {
            "Key": {"pk": _pk(order.tenant_id), "sk": "COUNTER"},
            "UpdateExpression": "ADD order_count :one",
            "ExpressionAttributeValues": {":one": 1},
        }
        if max_orders is not None:
            counter["ConditionExpression"] = "attribute_not_exists(order_count) OR order_count < :max"
            counter["ExpressionAttributeValues"][":max"] = max_orders
        item = {"pk": _pk(order.tenant_id), "sk": f"ORDER#{order.order_id}", **order.model_dump(mode="json")}
        transact(
            self.table,
            limit={"Update": counter},
            order={"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(pk)"}},
            event=outbox_put(event),
        )

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        # Strongly consistent, so a status change sees the previous one (e.g. "paid" right before "shipped").
        item = self.table.get_item(Key={"pk": _pk(tenant_id), "sk": f"ORDER#{order_id}"},
                                   ConsistentRead=True).get("Item")
        return Order.model_validate(_clean(item)) if item else None

    def list(self, tenant_id: str, limit: int, cursor: str | None = None) -> tuple[list[Order], str | None]:
        """Newest first. Returns the orders and a cursor for the next page (None at the end)."""
        params = {
            "KeyConditionExpression": Key("pk").eq(_pk(tenant_id)) & Key("sk").begins_with("ORDER#"),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:  # the pk comes from the token, so a cursor can't reach another tenant
            params["ExclusiveStartKey"] = {"pk": _pk(tenant_id), "sk": f"ORDER#{cursor}"}
        result = self.table.query(**params)
        last = result.get("LastEvaluatedKey")
        orders = [Order.model_validate(_clean(i)) for i in result["Items"]]
        return orders, last["sk"].removeprefix("ORDER#") if last else None

    def update_status(self, tenant_id: str, order_id: str, status: OrderStatus,
                      expected: OrderStatus) -> bool:
        """False if the status is no longer `expected` (someone else changed it)."""
        try:
            self.table.update_item(
                Key={"pk": _pk(tenant_id), "sk": f"ORDER#{order_id}"},
                UpdateExpression="SET #s = :s",
                ConditionExpression="#s = :expected",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": status.value, ":expected": expected.value},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
