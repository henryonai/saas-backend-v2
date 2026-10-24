"""
Assistant table:
  pk = TENANT#<tenant_id>   sk = USAGE#<YYYY-MM>   the workspace's usage that month

The tenant is part of every key, so one workspace never sees another's usage.
Records expire (TTL) about 13 months after the month started.
"""
import time

from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure

from assistant.models import Usage

USAGE_TTL_SECONDS = 400 * 24 * 60 * 60

# Every attribute goes through ExpressionAttributeNames: several are DynamoDB reserved words.
_NAMES = {"#tenant": "tenant_id", "#month": "month", "#messages": "messages", "#tokens": "tokens",
          "#input": "input_tokens", "#output": "output_tokens", "#sandboxes": "sandbox_sessions",
          "#expires": "expires_at"}


def _names_in(*expressions: str) -> dict:
    """The names these expressions use: DynamoDB rejects unused ones."""
    return {k: v for k, v in _NAMES.items() if any(k in e for e in expressions)}


class UsageLimitReached(Exception):
    """The workspace is at one of its limits. `usage` is what it has used so far."""

    def __init__(self, usage: Usage):
        super().__init__(f"Usage limit reached for tenant {usage.tenant_id}")
        self.usage = usage


def _key(tenant_id: str, month: str) -> dict:
    return {"pk": f"TENANT#{tenant_id}", "sk": f"USAGE#{month}"}


def _usage(item: dict) -> Usage:
    return Usage.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk", "expires_at")})


class UsageRepository:
    def __init__(self, table):
        self.table = table

    def add_message(self, tenant_id: str, month: str, max_messages: int | None, max_tokens: int | None) -> Usage:
        """
        Counts one message, but only while the workspace is under both limits
        (None = unlimited). It is one conditional update, so two requests at
        the same time can't both slip past a limit. Raises UsageLimitReached.
        """
        values = {":one": 1, ":tenant": tenant_id, ":month": month,
                  ":expires": int(time.time()) + USAGE_TTL_SECONDS}
        conditions = []
        if max_messages is not None:
            conditions.append("(attribute_not_exists(#messages) OR #messages < :max_messages)")
            values[":max_messages"] = max_messages
        if max_tokens is not None:
            conditions.append("(attribute_not_exists(#tokens) OR #tokens < :max_tokens)")
            values[":max_tokens"] = max_tokens
        update = ("ADD #messages :one "
                  "SET #tenant = :tenant, #month = :month, #expires = if_not_exists(#expires, :expires)")
        condition = " AND ".join(conditions)
        params = {
            "Key": _key(tenant_id, month),
            "UpdateExpression": update,
            "ExpressionAttributeNames": _names_in(update, condition),
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if condition:
            params["ConditionExpression"] = condition
        try:
            return _usage(self.table.update_item(**params)["Attributes"])
        except ClientError as e:
            if is_condition_failure(e):
                raise UsageLimitReached(self.get(tenant_id, month)) from e
            raise

    def add_tokens(self, tenant_id: str, month: str, input_tokens: int, output_tokens: int) -> Usage:
        """Adds one model call's tokens and returns the month's new totals."""
        update = ("ADD #tokens :total, #input :input, #output :output "
                  "SET #tenant = :tenant, #month = :month, #expires = if_not_exists(#expires, :expires)")
        result = self.table.update_item(
            Key=_key(tenant_id, month),
            UpdateExpression=update,
            ExpressionAttributeNames=_names_in(update),
            ExpressionAttributeValues={":total": input_tokens + output_tokens, ":input": input_tokens,
                                       ":output": output_tokens, ":tenant": tenant_id, ":month": month,
                                       ":expires": int(time.time()) + USAGE_TTL_SECONDS},
            ReturnValues="ALL_NEW",
        )
        return _usage(result["Attributes"])

    def add_sandbox_session(self, tenant_id: str, month: str, max_sessions: int | None) -> Usage:
        """Counts one sandbox session, but only under max_sessions (None = unlimited). Raises UsageLimitReached."""
        update = ("ADD #sandboxes :one "
                  "SET #tenant = :tenant, #month = :month, #expires = if_not_exists(#expires, :expires)")
        params = {
            "Key": _key(tenant_id, month),
            "UpdateExpression": update,
            "ExpressionAttributeValues": {":one": 1, ":tenant": tenant_id, ":month": month,
                                          ":expires": int(time.time()) + USAGE_TTL_SECONDS},
            "ReturnValues": "ALL_NEW",
        }
        if max_sessions is not None:
            params["ConditionExpression"] = "attribute_not_exists(#sandboxes) OR #sandboxes < :max"
            params["ExpressionAttributeValues"][":max"] = max_sessions
        params["ExpressionAttributeNames"] = _names_in(update, params.get("ConditionExpression", ""))
        try:
            return _usage(self.table.update_item(**params)["Attributes"])
        except ClientError as e:
            if is_condition_failure(e):
                raise UsageLimitReached(self.get(tenant_id, month)) from e
            raise

    def get(self, tenant_id: str, month: str) -> Usage:
        item = self.table.get_item(Key=_key(tenant_id, month), ConsistentRead=True).get("Item")
        return _usage(item) if item else Usage(tenant_id=tenant_id, month=month)
