"""
Audit table:
  pk = TENANT#<tenant_id>   sk = AUDIT#<occurred_at>#<event_id>   one entry (models.AuditEntry),
                                                                   newest last; removed by TTL
The tenant is part of every key, so one workspace never sees another's log.
"""
import base64
import json

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.dynamo import delete_partition, is_condition_failure

from audit.models import AuditEntry


def _pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


class InvalidCursor(ValueError):
    pass


class AuditRepository:
    def __init__(self, table):
        self.table = table

    def add(self, entry: AuditEntry, expires_at: int) -> bool:
        """False if this event has an entry already (delivered twice)."""
        try:
            self.table.put_item(
                Item={"pk": _pk(entry.tenant_id), "sk": f"AUDIT#{entry.occurred_at}#{entry.event_id}",
                      **entry.model_dump(), "expires_at": expires_at},
                ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def page(self, tenant_id: str, limit: int, cursor: str | None = None) -> tuple[list[AuditEntry], str | None]:
        """Newest first, and a cursor for the next page (None at the end)."""
        params = {"KeyConditionExpression": Key("pk").eq(_pk(tenant_id)) & Key("sk").begins_with("AUDIT#"),
                  "ScanIndexForward": False, "Limit": limit}
        if cursor:
            try:
                (sk,) = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            except (ValueError, TypeError) as e:
                raise InvalidCursor(cursor) from e
            if not isinstance(sk, str) or not sk.startswith("AUDIT#"):
                raise InvalidCursor(cursor)
            params["ExclusiveStartKey"] = {"pk": _pk(tenant_id), "sk": sk}   # the pk comes from the token
        result = self.table.query(**params)
        last = result.get("LastEvaluatedKey")
        next_cursor = base64.urlsafe_b64encode(json.dumps([last["sk"]]).encode()).decode().rstrip("=") \
            if last else None
        entries = [AuditEntry.model_validate({k: v for k, v in i.items() if k not in ("pk", "sk", "expires_at")})
                   for i in result["Items"]]
        return entries, next_cursor

    def all(self, tenant_id: str) -> list[AuditEntry]:
        """Every entry, newest first (for an export)."""
        entries, cursor = [], None
        while True:
            page, cursor = self.page(tenant_id, 500, cursor)
            entries += page
            if not cursor:
                return entries

    def delete_tenant(self, tenant_id: str) -> int:
        return delete_partition(self.table, _pk(tenant_id))
