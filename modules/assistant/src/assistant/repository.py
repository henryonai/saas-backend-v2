"""
Assistant table:
  pk = TENANT#<t>                         sk = USAGE#<YYYY-MM>   the workspace's usage that month
  pk = TENANT#<t>#USER#<u>#CONV#<thread>  sk = META              a conversation (models.Conversation),
                                                                 with its run while one answers
  pk = TENANT#<t>#USER#<u>#CONV#<thread>  sk = MSG#<000001>      one of its AG-UI messages, in order, up to
                                                                 META's message_count, with the agent it was
                                                                 said to or by, and the user's rating of an
                                                                 answer
  pk = TENANT#<t>                         sk = USER#<u>#HOUR#<YYYY-MM-DDTHH>  messages a user sent that hour (UTC)
  gsi1pk = TENANT#<t>#USER#<u>#CONVS      gsi1sk = <updated_at>  on META only: the user's list, newest first

The tenant is part of every key, and the user of every conversation key, so
one workspace never sees another's usage and one user never sees another's
conversations. Usage expires (TTL) about 13 months after the month started,
a user's hourly count an hour after its hour.
A conversation is kept until its user deletes it, or leaves the workspace
(purge.py). A stack can have it expire instead (CONVERSATION_DAYS), whole,
some days after its last message: only its META has a TTL (expires_at, moved
on by every message), so a long conversation never loses its oldest
messages. When TTL removes the META, the table's stream has the purge worker
delete the messages and the agents' checkpoints; until then, reads treat the
conversation as gone.
"""
import base64
import json
import time
from collections.abc import Iterator

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure

from assistant.models import Conversation, Rating, Run, TitleSource, Usage

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

    def add_hourly_message(self, tenant_id: str, user_id: str, hour: str, max_messages: int, expires_at: int) -> bool:
        """
        Counts one message the user sent this hour (UTC, e.g. "2026-10-25T09"),
        but only under max_messages: one conditional update, like the monthly
        count. False at the limit.
        """
        try:
            self.table.update_item(
                Key={"pk": f"TENANT#{tenant_id}", "sk": f"USER#{user_id}#HOUR#{hour}"},
                UpdateExpression="ADD #messages :one SET #expires = if_not_exists(#expires, :expires)",
                ConditionExpression="attribute_not_exists(#messages) OR #messages < :max",
                ExpressionAttributeNames={"#messages": "messages", "#expires": "expires_at"},
                ExpressionAttributeValues={":one": 1, ":max": max_messages, ":expires": expires_at},
            )
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        return True


def _conversation_pk(tenant_id: str, user_id: str, thread_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}#CONV#{thread_id}"


def conversation_of(pk: str) -> tuple[str, str, str]:
    """(tenant_id, user_id, thread_id) of a conversation's key. Raises ValueError for any other key."""
    rest, _, thread_id = pk.removeprefix("TENANT#").rpartition("#CONV#")
    tenant_id, _, user_id = rest.partition("#USER#")
    if not (pk.startswith("TENANT#") and tenant_id and user_id and thread_id):
        raise ValueError(f"Not a conversation's key: {pk!r}")
    return tenant_id, user_id, thread_id


def _list_pk(tenant_id: str, user_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}#CONVS"


def _message_sk(position: int) -> str:
    return f"MSG#{position:06d}"


def _conversation(item: dict) -> Conversation:
    return Conversation.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk", "gsi1pk", "gsi1sk")})


class InvalidCursor(ValueError):
    pass


class ConversationRepository:
    """
    Conversations and their messages. Every method takes the tenant and user
    from the caller's token, so a thread id from a client can only ever name
    one of that user's own conversations.
    """

    def __init__(self, table):
        self.table = table

    def add_question(self, tenant_id: str, user_id: str, thread_id: str, *, message: dict, run: Run, title: str,
                     at: int | None, count: int, now: str, expires_at: int | None, stale_before: str) -> bool:
        """
        The user's message and the run that will answer it: creates the
        conversation the first time (titled `title`), adds the message as
        the newest, or puts it `at` a position in place of the message there
        and everything after it (edited, or sent again), and sets the run.
        `count` is the message_count the caller read: False if it changed
        meanwhile, or while an earlier run, started after `stale_before`, is
        still answering. `expires_at` None: the conversation is kept.
        """
        position = at if at is not None else count + 1
        names = {"#thread": "thread_id", "#title": "title", "#source": "title_source", "#created": "created_at",
                 "#updated": "updated_at", "#expires": "expires_at", "#agents": "agents", "#run": "run",
                 "#question": "question_id", "#count": "message_count"}
        values = {":thread": thread_id, ":title": title, ":message": "message", ":now": now,
                  ":list": _list_pk(tenant_id, user_id), ":agents": {run.agent}, ":run": run.model_dump(),
                  ":question": message["id"], ":stale": stale_before, ":position": position, ":count": count}
        expiry, remove = _expiry(expires_at, values)
        update = ("SET #thread = :thread, #title = if_not_exists(#title, :title), "
                  "#source = if_not_exists(#source, :message), #created = if_not_exists(#created, :now), "
                  f"#updated = :now{expiry}, gsi1pk = :list, gsi1sk = :now, #run = :run, "
                  f"#question = :question, #count = :position{remove} ADD #agents :agents")
        condition = ("(attribute_not_exists(#run) OR #run.since < :stale) "
                     "AND (attribute_not_exists(#count) OR #count = :count)")
        try:
            self.table.update_item(
                Key={"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"},
                UpdateExpression=update,
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        self._put_messages(tenant_id, user_id, thread_id, [message], position, run.agent)
        # What came after it is gone from the conversation: message_count says
        # so already, and reads stop there, so a delete that fails half way
        # leaves nothing anyone sees, and later messages write over it.
        self._delete_messages(tenant_id, user_id, thread_id, range(position + 1, count + 1))
        return True

    def add_answer(self, tenant_id: str, user_id: str, thread_id: str, run_id: str, messages: list[dict], *,
                   agent: str, now: str, expires_at: int | None) -> bool:
        """
        After the run: appends what it streamed (maybe nothing), as `agent`'s,
        then removes the run, in that order, so whoever finds it answered
        finds the whole answer. False if the conversation no longer exists
        (deleted meanwhile).
        """
        key = {"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"}
        try:
            if messages:
                values = {":now": now, ":n": len(messages)}
                expiry, remove = _expiry(expires_at, values)
                result = self.table.update_item(
                    Key=key,
                    UpdateExpression=f"SET #updated = :now{expiry}, gsi1sk = :now{remove} ADD #count :n",
                    ConditionExpression="attribute_exists(pk)",
                    ExpressionAttributeNames={"#updated": "updated_at", "#expires": "expires_at",
                                              "#count": "message_count"},
                    ExpressionAttributeValues=values,
                    ReturnValues="UPDATED_NEW",
                )
                self._put_messages(tenant_id, user_id, thread_id, messages, int(result["Attributes"]["message_count"]),
                                   agent)
            self.table.update_item(Key=key, UpdateExpression="REMOVE #run", ConditionExpression="#run.run_id = :run",
                                   ExpressionAttributeNames={"#run": "run"},
                                   ExpressionAttributeValues={":run": run_id})
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        return True

    def cancel(self, tenant_id: str, user_id: str, thread_id: str, run_id: str) -> bool:
        """Asks the run to stop. False if it isn't the conversation's run any more: it ended."""
        try:
            self.table.update_item(
                Key={"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"},
                UpdateExpression="SET #run.cancelled = :yes",
                ConditionExpression="#run.run_id = :run",
                ExpressionAttributeNames={"#run": "run"},
                ExpressionAttributeValues={":yes": True, ":run": run_id},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def _put_messages(self, tenant_id: str, user_id: str, thread_id: str, messages: list[dict], last: int,
                      agent: str) -> None:
        """
        Numbers the messages so that the last one is `last` (the conversation's
        message_count), each with the agent it was said to or by. No TTL of
        their own: they go with the conversation.
        """
        pk = _conversation_pk(tenant_id, user_id, thread_id)
        first = last - len(messages) + 1
        with self.table.batch_writer() as batch:
            for n, message in enumerate(messages, start=first):
                batch.put_item(Item={"pk": pk, "sk": _message_sk(n), "message": message, "agent": agent})

    def _delete_messages(self, tenant_id: str, user_id: str, thread_id: str, positions: range) -> None:
        pk = _conversation_pk(tenant_id, user_id, thread_id)
        with self.table.batch_writer() as batch:
            for n in positions:
                batch.delete_item(Key={"pk": pk, "sk": _message_sk(n)})

    def find_message(self, tenant_id: str, user_id: str, thread_id: str, message_id: str,
                     last: int) -> tuple[int, str] | None:
        """
        Where the message with this (AG-UI) id is, and its role: (position,
        role), or None. Looks from the newest back, since messages edited or
        rated are mostly recent; `last` is the conversation's message_count.
        """
        if last < 1:
            return None
        params = {
            "KeyConditionExpression": Key("pk").eq(_conversation_pk(tenant_id, user_id, thread_id))
            & Key("sk").between(_message_sk(1), _message_sk(last)),
            "ProjectionExpression": "sk, #m.#id, #m.#role",
            "ExpressionAttributeNames": {"#m": "message", "#id": "id", "#role": "role"},
            "ScanIndexForward": False,
            "ConsistentRead": True,
        }
        while True:
            result = self.table.query(**params)
            for item in result["Items"]:
                if item["message"].get("id") == message_id:
                    return int(item["sk"].removeprefix("MSG#")), item["message"].get("role")
            if "LastEvaluatedKey" not in result:
                return None
            params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def rate(self, tenant_id: str, user_id: str, thread_id: str, position: int, rating: Rating | None) -> bool:
        """Sets (or, with None, clears) the user's rating of the message at `position`. False if it's gone."""
        key = {"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": _message_sk(position)}
        params = {"UpdateExpression": "REMOVE #rating", "ExpressionAttributeNames": {"#rating": "rating"}}
        if rating is not None:
            params = {"UpdateExpression": "SET #rating = :rating", "ExpressionAttributeNames": {"#rating": "rating"},
                      "ExpressionAttributeValues": {":rating": rating}}
        try:
            self.table.update_item(Key=key, ConditionExpression="attribute_exists(pk)", **params)
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        return True

    def set_title(self, tenant_id: str, user_id: str, thread_id: str, title: str, source: TitleSource,
                  only_over: TitleSource | None = None) -> bool:
        """
        Renames the conversation. With `only_over`, only while its title still
        comes from there (the model never replaces a title the user chose).
        False if there is no such conversation, or the condition failed.
        """
        condition, values = "attribute_exists(pk)", {":title": title, ":source": source}
        if only_over:
            condition += " AND #source = :only"
            values[":only"] = only_over
        try:
            self.table.update_item(
                Key={"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"},
                UpdateExpression="SET #title = :title, #source = :source",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#title": "title", "#source": "title_source"},
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def get(self, tenant_id: str, user_id: str, thread_id: str, *, expired: bool = False) -> Conversation | None:
        """
        The conversation, or None. TTL removes an expired one within days, not
        at once: it is hidden meanwhile, unless `expired`.
        """
        item = self.table.get_item(Key={"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"},
                                   ConsistentRead=True).get("Item")
        if not item or not expired and "expires_at" in item and item["expires_at"] <= time.time():
            return None
        return _conversation(item)

    def messages(self, tenant_id: str, user_id: str, thread_id: str, limit: int,
                 last: int | None = None) -> list[dict]:
        """The latest `limit` messages, oldest first: all that were written, even a moment ago."""
        return self.history(tenant_id, user_id, thread_id, limit, last)[0]

    def history(self, tenant_id: str, user_id: str, thread_id: str, limit: int,
                last: int | None = None) -> tuple[list[dict], dict[str, Rating]]:
        """
        The latest `limit` messages, oldest first, and the user's ratings of
        them by message id. `last`: the conversation's message_count, past
        which nothing belongs to it any more (add_question).
        """
        if last is not None and last < 1:
            return [], {}
        newest = Key("sk").between(_message_sk(1), _message_sk(last)) if last is not None \
            else Key("sk").begins_with("MSG#")
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_conversation_pk(tenant_id, user_id, thread_id)) & newest,
            ScanIndexForward=False,
            Limit=limit,
            ConsistentRead=True,
        )
        items = list(reversed(result["Items"]))
        return [item["message"] for item in items], {item["message"]["id"]: item["rating"] for item in items
                                                      if "rating" in item}

    def all_messages(self, tenant_id: str, user_id: str, thread_id: str,
                     last: int) -> list[tuple[str | None, dict]]:
        """
        Every message up to `last` (the conversation's message_count), oldest
        first, with the agent it was said to or by (None: saved before
        messages had one).
        """
        if last < 1:
            return []
        params = {
            "KeyConditionExpression": Key("pk").eq(_conversation_pk(tenant_id, user_id, thread_id))
            & Key("sk").between(_message_sk(1), _message_sk(last)),
            "ConsistentRead": True,
        }
        said = []
        while True:
            result = self.table.query(**params)
            said += [(item.get("agent"), item["message"]) for item in result["Items"]]
            if "LastEvaluatedKey" not in result:
                return said
            params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def list(self, tenant_id: str, user_id: str, limit: int,
             cursor: str | None = None) -> tuple[list[Conversation], str | None]:
        """Newest first. Returns the conversations and a cursor for the next page (None at the end)."""
        params = {
            "IndexName": "gsi1",
            "KeyConditionExpression": Key("gsi1pk").eq(_list_pk(tenant_id, user_id)),
            "FilterExpression": Attr("expires_at").not_exists() | Attr("expires_at").gt(int(time.time())),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:
            # Only the thread id and time come from the cursor: the user's keys come from the token.
            thread_id, updated_at = _read_cursor(cursor)
            params["ExclusiveStartKey"] = {"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META",
                                           "gsi1pk": _list_pk(tenant_id, user_id), "gsi1sk": updated_at}
        result = self.table.query(**params)
        last = result.get("LastEvaluatedKey")
        next_cursor = _cursor(last["pk"].rsplit("#CONV#", 1)[1], last["gsi1sk"]) if last else None
        return [_conversation(item) for item in result["Items"]], next_cursor

    def last_used_by(self, tenant_id: str, user_id: str, until: str) -> Iterator[Conversation]:
        """
        Every conversation of the user's in the workspace, expired or not,
        whose last message was at or before `until` (an ISO time, as stored).
        """
        params = {"IndexName": "gsi1",
                  "KeyConditionExpression": Key("gsi1pk").eq(_list_pk(tenant_id, user_id)) & Key("gsi1sk").lte(until)}
        while True:
            result = self.table.query(**params)
            yield from (_conversation(item) for item in result["Items"])
            if "LastEvaluatedKey" not in result:
                return
            params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def delete(self, tenant_id: str, user_id: str, thread_id: str) -> int:
        """Removes the conversation and all its messages (whatever is left of them). Returns how many records."""
        params = {"KeyConditionExpression": Key("pk").eq(_conversation_pk(tenant_id, user_id, thread_id)),
                  "ProjectionExpression": "pk, sk"}
        deleted = 0
        with self.table.batch_writer() as batch:
            while True:
                result = self.table.query(**params)
                for item in result["Items"]:
                    batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                deleted += len(result["Items"])
                if "LastEvaluatedKey" not in result:
                    break
                params["ExclusiveStartKey"] = result["LastEvaluatedKey"]
        return deleted


def _expiry(expires_at: int | None, values: dict) -> tuple[str, str]:
    """
    The parts of an update that set the conversation's TTL to `expires_at`,
    or remove it (None: kept until its user deletes it): (", #expires =
    :expires" for SET, or " REMOVE #expires" to go before ADD).
    """
    if expires_at is None:
        return "", " REMOVE #expires"
    values[":expires"] = expires_at
    return ", #expires = :expires", ""


def _cursor(thread_id: str, updated_at: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([thread_id, updated_at]).encode()).decode().rstrip("=")


def _read_cursor(cursor: str) -> tuple[str, str]:
    try:
        thread_id, updated_at = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as e:   # not base64, not JSON, or not a pair
        raise InvalidCursor(cursor) from e
    if not (isinstance(thread_id, str) and isinstance(updated_at, str)):
        raise InvalidCursor(cursor)
    return thread_id, updated_at
