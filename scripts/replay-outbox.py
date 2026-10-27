"""
Publishes a module's saved events again, e.g. after the OutboxDLQ alarm (the
relay could not publish them within an hour). Events
stay in the outbox for 7 days. Listeners skip events they already handled, so
replaying more than needed is safe.

    uv run python scripts/replay-outbox.py --table <table name> --bus <EventBusName output> \\
        --since 2026-10-22T08:00:00+00:00 [--dry-run]
"""
import argparse
import json
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Attr

from shared.events import EventBridgePublisher

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--table", required=True)
parser.add_argument("--bus", required=True)
parser.add_argument("--since", required=True, type=datetime.fromisoformat, help="ISO time with offset")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

table = boto3.resource("dynamodb").Table(args.table)
publisher = EventBridgePublisher(boto3.client("events"), args.bus)
scan = {"FilterExpression": Attr("pk").begins_with("OUTBOX#")}
replayed = 0
while True:
    page = table.scan(**scan)
    for item in page["Items"]:
        detail = json.loads(item["detail"])
        if datetime.fromisoformat(detail["occurred_at"]) >= args.since:
            print(f"{detail['occurred_at']} {item['event_type']} {detail['event_id']}")
            if not args.dry_run:
                publisher.publish(item["event_type"], detail)
            replayed += 1
    if "LastEvaluatedKey" not in page:
        break
    scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
print(f"{'Would replay' if args.dry_run else 'Replayed'} {replayed} events")
