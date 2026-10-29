# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3"]
# ///
"""
Writes the status copy (shared/workspace_status.py) of workspaces suspended or
being deleted before orders, billing and the assistant kept one, so they
refuse changes too. From then on their listeners keep it from events. Safe to
run again: it writes the status accounts has now, and a newer event wins.

    uv run scripts/backfill-workspace-status.py [--stack saas-dev] [--dry-run]

Reads the accounts table and writes to the orders, billing and (if the stack
has it) assistant tables (a one-off migration: modules themselves never touch
another's table) with your AWS credentials.
"""
import argparse
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--stack", default="saas-dev")
parser.add_argument("--dry-run", action="store_true", help="print what it would write")
args = parser.parse_args()

cloudformation = boto3.client("cloudformation")
dynamodb = boto3.resource("dynamodb")


def table(logical_id: str):
    try:
        resource = cloudformation.describe_stack_resource(StackName=args.stack, LogicalResourceId=logical_id)
    except ClientError:
        return None   # the assistant is optional
    return dynamodb.Table(resource["StackResourceDetail"]["PhysicalResourceId"])


accounts = table("AccountsTable")
copies = [t for t in (table("OrdersTable"), table("BillingTable"), table("AssistantTable")) if t]


def inactive_profiles():
    params = {"FilterExpression": Attr("sk").eq("PROFILE") & Attr("status").is_in(["suspended", "deleting"])}
    while True:
        page = accounts.scan(**params)
        yield from page["Items"]
        if "LastEvaluatedKey" not in page:
            return
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# Older than any event to come, so a later unsuspend or restore still wins.
at = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
written = 0
for profile in inactive_profiles():
    item = {"pk": f"TENANT#{profile['tenant_id']}", "sk": "STATUS", "status": profile["status"], "at": at}
    for copy in copies:
        if args.dry_run:
            print(copy.name, item)
        else:
            try:
                copy.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")   # an event's copy wins
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
    written += 1

print(f"{'Would write' if args.dry_run else 'Wrote'} the status of {written} workspaces to "
      f"{', '.join(c.name for c in copies)}")
