"""
Billing table:
  pk = TENANT#<tenant_id>   sk = SUBSCRIPTION   -> subscription
  pk = WEBHOOK#<event_id>   sk = WEBHOOK        -> processed webhook (idempotency)
"""
from botocore.exceptions import ClientError

from billing.models import Subscription


def _key(tenant_id: str) -> dict:
    return {"pk": f"TENANT#{tenant_id}", "sk": "SUBSCRIPTION"}


class SubscriptionRepository:
    def __init__(self, table):
        self.table = table

    def get(self, tenant_id: str) -> Subscription | None:
        item = self.table.get_item(Key=_key(tenant_id)).get("Item")
        if not item:
            return None
        return Subscription.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk")})

    def save_owner(self, tenant_id: str, owner_id: str, owner_email: str) -> None:
        """Creates the record if missing; never overwrites plan/status."""
        self.table.update_item(
            Key=_key(tenant_id),
            UpdateExpression=("SET tenant_id = :t, owner_id = :oid, owner_email = :oem, "
                              "#p = if_not_exists(#p, :free), #s = if_not_exists(#s, :active)"),
            ExpressionAttributeNames={"#p": "plan", "#s": "status"},
            ExpressionAttributeValues={":t": tenant_id, ":oid": owner_id, ":oem": owner_email,
                                       ":free": "free", ":active": "active"},
        )

    def update(self, tenant_id: str, plan: str, status: str) -> None:
        self.table.update_item(
            Key=_key(tenant_id),
            UpdateExpression="SET tenant_id = :t, #p = :p, #s = :s",
            ExpressionAttributeNames={"#p": "plan", "#s": "status"},
            ExpressionAttributeValues={":t": tenant_id, ":p": plan, ":s": status},
        )


class ProcessedEventRepository:
    """Providers can send the same webhook twice; we handle each id once."""

    def __init__(self, table):
        self.table = table

    def mark(self, event_id: str) -> bool:
        """True the first time, False if already processed."""
        try:
            self.table.put_item(Item={"pk": f"WEBHOOK#{event_id}", "sk": "WEBHOOK"},
                                ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def unmark(self, event_id: str) -> None:
        """Undo a mark when processing failed, so the provider's retry works."""
        self.table.delete_item(Key={"pk": f"WEBHOOK#{event_id}", "sk": "WEBHOOK"})
