"""
Orders table:  pk = TENANT#<tenant_id>   sk = ORDER#<order_id>
The tenant is part of every key, so one tenant can never read another's orders.
"""
from boto3.dynamodb.conditions import Key

from orders.models import Order


def _pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


class OrderRepository:
    def __init__(self, table):
        self.table = table

    def save(self, order: Order) -> None:
        self.table.put_item(Item={"pk": _pk(order.tenant_id), "sk": f"ORDER#{order.order_id}",
                                  **order.model_dump(mode="json")})

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        item = self.table.get_item(Key={"pk": _pk(tenant_id), "sk": f"ORDER#{order_id}"}).get("Item")
        return Order.model_validate(_clean(item)) if item else None

    def list(self, tenant_id: str, limit: int) -> list[Order]:
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_pk(tenant_id)) & Key("sk").begins_with("ORDER#"),
            Limit=limit,
        )
        return [Order.model_validate(_clean(i)) for i in result["Items"]]

    def count(self, tenant_id: str) -> int:
        total, start_key = 0, None
        while True:  # COUNT queries are paginated too
            params = {
                "KeyConditionExpression": Key("pk").eq(_pk(tenant_id)) & Key("sk").begins_with("ORDER#"),
                "Select": "COUNT",
            }
            if start_key:
                params["ExclusiveStartKey"] = start_key
            result = self.table.query(**params)
            total += result["Count"]
            start_key = result.get("LastEvaluatedKey")
            if not start_key:
                return total

    def update_status(self, tenant_id: str, order_id: str, status: str) -> None:
        self.table.update_item(
            Key={"pk": _pk(tenant_id), "sk": f"ORDER#{order_id}"},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )
