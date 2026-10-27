"""
DynamoDB transactions: several writes that all succeed or all fail.

Used to save a change together with its events (outbox.py) and to enforce
limits and state rules with conditions, instead of reading first and writing
afterwards (two requests at the same time could both pass the read).
"""
import random
import time
from collections.abc import Callable

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.errors import ServiceBusy

ATTEMPTS = 4


class ConditionFailed(Exception):
    """A condition in a write failed. `failed` names the operations that failed."""

    def __init__(self, failed: set[str]):
        super().__init__(f"Condition failed: {', '.join(sorted(failed))}")
        self.failed = failed


def transact(table, **operations: dict | None) -> None:
    """
    Runs the named operations atomically on one table:

        transact(table, order={"Put": {...}}, event=outbox_put(event))

    Operations set to None are skipped. Raises ConditionFailed with the names
    of the operations whose ConditionExpression failed. A transaction that
    collides with another one on the same item is retried; if it keeps
    colliding, ServiceBusy (503) tells the caller to retry later.
    """
    named = [(name, op) for name, op in operations.items() if op is not None]
    items = [{kind: {"TableName": table.name, **params}}
             for _, op in named for kind, params in op.items()]
    for attempt in range(ATTEMPTS):
        try:
            table.meta.client.transact_write_items(TransactItems=items)
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            codes = [reason.get("Code") for reason in e.response.get("CancellationReasons", [])]
            failed = {name for (name, _), code in zip(named, codes, strict=False)
                      if code == "ConditionalCheckFailed"}
            if failed:
                raise ConditionFailed(failed) from e
            if "TransactionConflict" not in codes:
                raise
        time.sleep(random.uniform(0, 0.05 * 2 ** attempt))  # back off with jitter, then retry
    raise ServiceBusy("Too many changes at the same time. Please retry.")


def is_condition_failure(error: ClientError) -> bool:
    """True if a single-item write failed because of its ConditionExpression."""
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def delete_partition(table, pk: str, keep: Callable[[str], bool] = lambda sk: False) -> int:
    """
    Deletes every item under `pk` whose sort key `keep` doesn't keep, and
    returns how many. Safe to run again: what it deleted is gone (a purge
    that timed out continues where it stopped).
    """
    keys, start = [], {}
    while True:
        result = table.query(KeyConditionExpression=Key("pk").eq(pk), ProjectionExpression="pk, sk", **start)
        keys += [{"pk": i["pk"], "sk": i["sk"]} for i in result["Items"] if not keep(i["sk"])]
        if "LastEvaluatedKey" not in result:
            break
        start = {"ExclusiveStartKey": result["LastEvaluatedKey"]}
    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return len(keys)
