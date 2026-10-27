"""
Notifications table:
  pk = TENANT#<t>#USER#<u>      sk = NOTIF#<notification_id>
  pk = IDEMPOTENCY#<function>   sk = <hash>     processed events (Powertools, expire)
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.dynamo import delete_partition, is_condition_failure

from notifications.models import Notification


def _pk(tenant_id: str, user_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}"


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


class NotificationRepository:
    def __init__(self, table):
        self.table = table

    def save(self, n: Notification) -> bool:
        """False if a notification with this id already exists (a repeated event)."""
        try:
            self.table.put_item(Item={"pk": _pk(n.tenant_id, n.user_id),
                                      "sk": f"NOTIF#{n.notification_id}", **n.model_dump()},
                                ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def delete_all(self, tenant_id: str, user_id: str) -> int:
        """Deletes every notification of the user in the workspace. Safe to repeat."""
        return delete_partition(self.table, _pk(tenant_id, user_id))

    def list(self, tenant_id: str, user_id: str, limit: int) -> list[Notification]:
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_pk(tenant_id, user_id)) & Key("sk").begins_with("NOTIF#"),
            ScanIndexForward=False,  # newest first
            Limit=limit,
        )
        return [Notification.model_validate(_clean(i)) for i in result["Items"]]

    def mark_read(self, tenant_id: str, user_id: str, notification_id: str) -> bool:
        """False if it doesn't exist for this user."""
        try:
            self.table.update_item(
                Key={"pk": _pk(tenant_id, user_id), "sk": f"NOTIF#{notification_id}"},
                UpdateExpression="SET #r = :true",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#r": "read"},
                ExpressionAttributeValues={":true": True},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
