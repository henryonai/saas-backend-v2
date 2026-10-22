"""
The only code that touches the accounts table. The table is injected
(not looked up from a global), so tests pass a fake one.

Key design:
  pk                  sk               what
  TENANT#<tenant_id>  PROFILE          tenant
  TENANT#<tenant_id>  USER#<user_id>   member  (gsi1: USER#<user_id> -> tenant)
"""
from boto3.dynamodb.conditions import Key

from accounts.models import Member, Tenant

KEY_FIELDS = ("pk", "sk", "gsi1pk", "gsi1sk")


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in KEY_FIELDS}


def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


class TenantRepository:
    def __init__(self, table):
        self.table = table

    def save(self, tenant: Tenant) -> None:
        self.table.put_item(Item={"pk": _tenant_pk(tenant.tenant_id), "sk": "PROFILE",
                                  **tenant.model_dump()})

    def get(self, tenant_id: str) -> Tenant | None:
        item = self.table.get_item(Key={"pk": _tenant_pk(tenant_id), "sk": "PROFILE"}).get("Item")
        return Tenant.model_validate(_clean(item)) if item else None

    def update_subscription(self, tenant_id: str, plan: str, status: str) -> None:
        self.table.update_item(
            Key={"pk": _tenant_pk(tenant_id), "sk": "PROFILE"},
            UpdateExpression="SET #p = :p, subscription_status = :s",
            ExpressionAttributeNames={"#p": "plan"},
            ExpressionAttributeValues={":p": plan, ":s": status},
        )


class MemberRepository:
    def __init__(self, table):
        self.table = table

    def save(self, m: Member) -> None:
        self.table.put_item(Item={
            "pk": _tenant_pk(m.tenant_id), "sk": f"USER#{m.user_id}",
            "gsi1pk": f"USER#{m.user_id}", "gsi1sk": _tenant_pk(m.tenant_id),
            **m.model_dump(),
        })

    def get(self, tenant_id: str, user_id: str) -> Member | None:
        item = self.table.get_item(
            Key={"pk": _tenant_pk(tenant_id), "sk": f"USER#{user_id}"}
        ).get("Item")
        return Member.model_validate(_clean(item)) if item else None

    def list(self, tenant_id: str) -> list[Member]:
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_tenant_pk(tenant_id)) & Key("sk").begins_with("USER#")
        )
        return [Member.model_validate(_clean(i)) for i in result["Items"]]

    def find_by_user(self, user_id: str) -> Member | None:
        result = self.table.query(
            IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq(f"USER#{user_id}"), Limit=1,
        )
        return Member.model_validate(_clean(result["Items"][0])) if result["Items"] else None

    def update_role(self, tenant_id: str, user_id: str, role: str) -> None:
        self.table.update_item(
            Key={"pk": _tenant_pk(tenant_id), "sk": f"USER#{user_id}"},
            UpdateExpression="SET #r = :r",
            ExpressionAttributeNames={"#r": "role"},
            ExpressionAttributeValues={":r": role},
        )

    def delete(self, tenant_id: str, user_id: str) -> None:
        self.table.delete_item(Key={"pk": _tenant_pk(tenant_id), "sk": f"USER#{user_id}"})
