"""
Moves a stack deployed before users could belong to several workspaces to
the new accounts items: each user's one USER#<id> / MEMBERSHIP item becomes
USER#<id> / WORKSPACE#<tenant_id>, with the USER#<id> / WORKSPACES count
(modules/accounts/src/accounts/repository.py). A new stack doesn't need it.

    uv run python scripts/migrate-memberships.py copy --stack saas-dev      # before deploying the new code
    (deploy)
    uv run python scripts/migrate-memberships.py cleanup --stack saas-dev   # after: deletes the old items

The old code ignores the new items and the new code ignores the old ones, so
users keep their workspace throughout, and both steps can run again safely.
Add --dry-run to see what would change.
"""
import argparse

import boto3
from boto3.dynamodb.conditions import Attr, Key

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("step", choices=["copy", "cleanup"])
parser.add_argument("--stack", required=True, help="e.g. saas-dev")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

table_name = boto3.client("cloudformation").describe_stack_resource(
    StackName=args.stack, LogicalResourceId="AccountsTable")["StackResourceDetail"]["PhysicalResourceId"]
table = boto3.resource("dynamodb").Table(table_name)


def old_memberships():
    scan = {"FilterExpression": Attr("sk").eq("MEMBERSHIP")}
    while True:
        page = table.scan(**scan)
        yield from page["Items"]
        if "LastEvaluatedKey" not in page:
            return
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def workspaces_of(user_pk: str) -> list[str]:
    result = table.query(KeyConditionExpression=Key("pk").eq(user_pk) & Key("sk").begins_with("WORKSPACE#"),
                         ConsistentRead=True)
    return [item["tenant_id"] for item in result["Items"]]


def stored_count(user_pk: str) -> int | None:
    item = table.get_item(Key={"pk": user_pk, "sk": "WORKSPACES"}, ConsistentRead=True).get("Item")
    return int(item["count"]) if item else None


changed = 0
for item in old_memberships():
    user_pk, tenant_id = item["pk"], item["tenant_id"]
    has_new = tenant_id in workspaces_of(user_pk)
    if args.step == "copy":
        # Also repairs a run that stopped between the two writes: the count must match the items.
        if has_new and stored_count(user_pk) == len(workspaces_of(user_pk)):
            continue
        print(f"{user_pk}: WORKSPACE#{tenant_id}")
        if not args.dry_run:
            table.put_item(Item={"pk": user_pk, "sk": f"WORKSPACE#{tenant_id}", "tenant_id": tenant_id})
            table.put_item(Item={"pk": user_pk, "sk": "WORKSPACES", "count": len(workspaces_of(user_pk))})
        changed += 1
    elif args.step == "cleanup":
        if not has_new:   # never delete a membership that wasn't copied
            print(f"{user_pk}: not copied yet, kept (run copy first)")
            continue
        print(f"{user_pk}: delete MEMBERSHIP")
        if not args.dry_run:
            table.delete_item(Key={"pk": user_pk, "sk": "MEMBERSHIP"})
        changed += 1

print(f"{'Would change' if args.dry_run else 'Changed'} {changed} users in {table_name} ({args.step}).")
