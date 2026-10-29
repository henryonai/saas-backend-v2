# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3"]
# ///
"""
Fills the back-office's workspace summaries (the admin table) for workspaces
created before the admin module existed. From then on its listener keeps them
up to date from events. Safe to run again: it writes each summary whole.

    uv run scripts/backfill-admin.py [--stack saas-dev] [--dry-run]

Reads the accounts and orders tables (a one-off migration: modules themselves
never read another's table) with your AWS credentials.
"""
import argparse

import boto3
from boto3.dynamodb.conditions import Attr

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--stack", default="saas-dev")
parser.add_argument("--dry-run", action="store_true", help="print what it would write")
args = parser.parse_args()

cloudformation = boto3.client("cloudformation")
dynamodb = boto3.resource("dynamodb")


def table(logical_id: str):
    resource = cloudformation.describe_stack_resource(StackName=args.stack, LogicalResourceId=logical_id)
    return dynamodb.Table(resource["StackResourceDetail"]["PhysicalResourceId"])


accounts, orders, admin = table("AccountsTable"), table("OrdersTable"), table("AdminTable")


def profiles():
    params = {"FilterExpression": Attr("sk").eq("PROFILE")}
    while True:
        page = accounts.scan(**params)
        yield from page["Items"]
        if "LastEvaluatedKey" not in page:
            return
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]


written = 0
for profile in profiles():
    status = profile.get("status", "active")
    if status == "purging":
        continue   # going: TenantDeleted removes it from the back-office
    tenant_id = profile["tenant_id"]
    counter = orders.get_item(Key={"pk": f"TENANT#{tenant_id}", "sk": "COUNTER"}).get("Item", {})
    summary = {
        "pk": "TENANTS", "sk": tenant_id, "tenant_id": tenant_id, "name": profile["name"],
        "owner_id": profile["owner_id"], "created_at": profile["created_at"], "plan": profile.get("plan", "free"),
        "subscription_status": profile.get("subscription_status", "active"),
        "revision": profile.get("subscription_revision", 0), "comped": False,
        "status": status, "members": int(profile.get("seats_used", 1)),
        "orders": int(counter.get("order_count", 0)),
    }
    if args.dry_run:
        print(summary)
    else:
        admin.put_item(Item=summary)
    written += 1

# The listener applies only a newer plan revision, so events after this run still win.
print(f"{'Would write' if args.dry_run else 'Wrote'} {written} workspace summaries to {admin.name}")
