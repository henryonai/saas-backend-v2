"""
Idempotency keys: a create a client may retry (a timeout, a 503, a double
click) sends an Idempotency-Key header, a new UUID per thing it creates. The
key is saved in the same transaction as what it creates, so a retry either
finds it or finds nothing written:

  pk = <the module's pk for the workspace>   sk = IDEMPOTENCY#<user_id>#<key>
      the id of what the first request created, and its request's fingerprint
      (removed by DynamoDB TTL after 24 hours)

A route needs one only when the server picks the new id (an order). Writes
with a natural key (an invitation per email) or a condition on the current
state (a status change) are already safe to repeat. Use:

    create(..., idempotency=idempotency_put(pk, user_id, key, fingerprint(body), new_id))
    # the transaction failed ConditionFailed({IDEMPOTENCY, ...}): a retry
    existing_id = replay(table, pk, user_id, key, fingerprint(body))
"""
import hashlib
import json
import time
from typing import Annotated

from aws_lambda_powertools.event_handler.openapi.params import Header

from shared.errors import Conflict, InvalidRequest

IDEMPOTENCY = "idempotency"   # its operation's name in dynamo.transact, and in ConditionFailed.failed
TTL_SECONDS = 24 * 60 * 60    # a client retries within minutes; a day is plenty

# The header, required: a create without one could not be retried safely.
IdempotencyKey = Annotated[str, Header(
    alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{1,128}$",
    description="A new random value (a UUID) for each thing created. Sent again with the same request, "
                "it returns what the first request created instead of creating another.",
)]


def fingerprint(body: dict) -> str:
    """The same for the same request body, whatever the order of its fields."""
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _key(pk: str, user_id: str, key: str) -> dict:
    return {"pk": pk, "sk": f"IDEMPOTENCY#{user_id}#{key}"}


def idempotency_put(pk: str, user_id: str, key: str, request: str, created_id: str) -> dict:
    """A transaction operation (see dynamo.transact) that saves the key; it fails if the key is in use."""
    now = int(time.time())
    return {"Put": {
        "Item": {**_key(pk, user_id, key), "created_id": created_id, "fingerprint": request,
                 "expires_at": now + TTL_SECONDS},
        # TTL removes it some time after it expires: until then, an expired one is free to reuse.
        "ConditionExpression": "attribute_not_exists(pk) OR expires_at <= :now",
        "ExpressionAttributeValues": {":now": now},
    }}


def replay(table, pk: str, user_id: str, key: str, request: str) -> str:
    """
    The id the first request with this key created. 400 if that request was
    a different one; 409 if the key expired just now (rare: retry).
    """
    item = table.get_item(Key=_key(pk, user_id, key), ConsistentRead=True).get("Item")
    if not item or item["expires_at"] <= int(time.time()):
        raise Conflict("This Idempotency-Key just expired. Please retry.")
    if item["fingerprint"] != request:
        raise InvalidRequest("This Idempotency-Key was used for a different request. Use a new key.")
    return item["created_id"]
