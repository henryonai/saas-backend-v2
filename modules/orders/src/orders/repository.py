"""
Orders table:
  pk = TENANT#<tenant_id>   sk = ORDER#<order_id>   order (ids sort by creation time)
  pk = TENANT#<tenant_id>   sk = COUNTER            order_count, for the plan limit
  pk = TENANT#<tenant_id>   sk = IDEMPOTENCY#<user_id>#<key>   the order a create with that Idempotency-Key
                                                    made (shared/idempotency.py)
  pk = TENANT#<tenant_id>   sk = STATUS             the workspace's status (no item: active)
                                                    (shared/workspace_status.py)
  pk = OUTBOX#<event_id>    sk = OUTBOX             events to publish (shared/outbox.py)

The tenant is part of every key, so one tenant can never read another's orders.
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.dynamo import delete_partition, is_condition_failure, transact
from shared.events import Event
from shared.idempotency import idempotency_put, replay
from shared.outbox import outbox_put
from shared.workspace_status import active_condition, check_active, save

from orders.models import Order, OrderStatus


def _pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


class OrderRepository:
    def __init__(self, table):
        self.table = table

    def delete_tenant(self, tenant_id: str) -> int:
        """Deletes every item of a deleted workspace (orders, counter, keys). Safe to repeat."""
        return delete_partition(self.table, _pk(tenant_id))

    def save_status(self, event_type: str, detail: dict) -> None:
        save(self.table, event_type, detail)

    def check_active(self, tenant_id: str) -> None:
        """Raises WorkspaceInactive (403) while the workspace is suspended or being deleted."""
        check_active(self.table, tenant_id)

    def create(self, order: Order, event: Event, max_orders: int | None, idempotency: tuple[str, str]) -> None:
        """
        Saves the order and its event, and counts it against the plan limit,
        all in one transaction. Raises ConditionFailed({"limit"}) when the
        tenant already has max_orders orders (None = unlimited), and
        ConditionFailed({"active"}) while the workspace isn't active.

        `idempotency` is the client's (key, request fingerprint): saved in the
        same transaction, so a retry with the key raises
        ConditionFailed({IDEMPOTENCY, ...}) and changes nothing (see order_for_key).
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
            idempotency=idempotency_put(_pk(order.tenant_id), order.created_by, *idempotency, order.order_id),
            active=active_condition(self.table, order.tenant_id),
        )

    def order_for_key(self, tenant_id: str, user_id: str, key: str, request: str) -> str:
        """The id of the order the first create with this key made (shared.idempotency.replay)."""
        return replay(self.table, _pk(tenant_id), user_id, key, request)

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
