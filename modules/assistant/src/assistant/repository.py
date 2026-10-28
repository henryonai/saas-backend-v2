"""
Assistant table:
  pk = TENANT#<t>                         sk = USAGE#<YYYY-MM>   the workspace's usage that month
  pk = TENANT#<t>#USER#<u>#CONV#<thread>  sk = META              a conversation (models.Conversation),
                                                                 with its run while one answers
  pk = TENANT#<t>#USER#<u>#CONV#<thread>  sk = MSG#<000001>      one of its AG-UI messages, at its position, with
                                                                 the agent it was said to or by, and the user's
                                                                 rating of an answer
  pk = TENANT#<t>#USER#<u>#CONV#<thread>  sk = BRANCH#<0001>#MSG#<000004>   the same, in a version other than
                                                                 the first (branch 1 from position 4: an edit,
                                                                 or an answer asked for again, versions.py)
  pk = TENANT#<t>                         sk = USER#<u>#HOUR#<YYYY-MM-DDTHH>  messages a user sent that hour (UTC)
  pk = TENANT#<t>                         sk = USER#<u>#UPLOADS#<YYYY-MM-DDTHH>  files they uploaded that hour
  pk = TENANT#<t>                         sk = STATUS            the workspace's status (no item: active)
                                                                (shared/workspace_status.py)
  pk = TENANT#<t>#USER#<u>#MEMORY         sk = MEM#<id>          something the assistant remembers about the user
                                                                 (models.Memory)
  pk = TENANT#<t>#USER#<u>#MEMORY         sk = SETTINGS          whether it remembers anything (enabled)
  pk = TENANT#<t>#USER#<u>#FILES          sk = FILE#<file_id>    one of the user's files (models.File), newest
                                                                 last (ids sort by time); the bytes are in S3
  pk = TENANT#<t>#USER#<u>#FILES          sk = NAMED#<thread>#<name>   the file an agent saved under that name
                                                                 in that conversation: saved again, a new version
  gsi1pk = TENANT#<t>#USER#<u>#CONVS      gsi1sk = <updated_at>  on META only: the user's list, newest first
                                         (index ConversationsByUser, with only LISTED of META's fields)

The tenant is part of every key, and the user of every conversation key, so
one workspace never sees another's usage and one user never sees another's
conversations. A conversation's META says which messages it shows: the
path through its versions (path, branches, message_count), each segment of
which is one query. Usage expires (TTL) about 13 months after the month started,
a user's hourly count an hour after its hour.
A conversation is kept until its user deletes it, or leaves the workspace
(purge.py); so are memories. A stack can have it expire instead (CONVERSATION_DAYS), whole,
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
from dataclasses import dataclass
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure
from shared.workspace_status import check_active

from assistant.models import Conversation, File, FileVersion, Memory, Rating, Run, TitleSource, Usage
from assistant.versions import Node, Tree

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

    def check_active(self, tenant_id: str) -> None:
        """Raises WorkspaceInactive (403) while the workspace is suspended or being deleted."""
        check_active(self.table, tenant_id)

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

    def add_hourly_message(self, tenant_id: str, user_id: str, hour: str, max_messages: int, expires_at: int,
                           counter: str = "HOUR") -> bool:
        """
        Counts one message the user sent this hour (UTC, e.g. "2026-10-25T09"),
        but only under max_messages: one conditional update, like the monthly
        count. False at the limit. `counter` UPLOADS counts files they attach.
        """
        try:
            self.table.update_item(
                Key={"pk": f"TENANT#{tenant_id}", "sk": f"USER#{user_id}#{counter}#{hour}"},
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


# The index of each user's conversations, and the fields of a META it keeps
# (template.yaml): what the list shows, what the purge needs (agents) and the
# expiry the list filters on. Conversations read from it have no run or versions.
CONVERSATIONS_BY_USER = "ConversationsByUser"
LISTED = ("thread_id", "title", "title_source", "created_at", "updated_at", "message_count", "agents", "expires_at")


def _list_pk(tenant_id: str, user_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}#CONVS"


def _message_sk(position: int, branch: int = 0) -> str:
    """A message's key: branch 0, the conversation as first written, has the keys it had before versions."""
    return f"MSG#{position:06d}" if branch == 0 else f"BRANCH#{branch:04d}#MSG#{position:06d}"


def _position(sk: str) -> int:
    return int(sk.rpartition("MSG#")[2])


def _plain(value):
    """A message as it was written: DynamoDB gives its numbers (a file's size) back as Decimal."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _conversation(item: dict) -> Conversation:
    return Conversation.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk", "gsi1pk", "gsi1sk")})


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class Found:
    """A message on the path a conversation shows: where it is, and its AG-UI role."""
    position: int
    role: str | None


class ConversationRepository:
    """
    Conversations and their messages. Every method takes the tenant and user
    from the caller's token, so a thread id from a client can only ever name
    one of that user's own conversations.
    """

    def __init__(self, table):
        self.table = table

    def add_question(self, tenant_id: str, user_id: str, thread_id: str, *, message: dict | None, run: Run,
                     title: str, before: Conversation | None, tree: Tree, now: str, expires_at: int | None,
                     stale_before: str) -> bool:
        """
        The user's message and the run that will answer it: creates the
        conversation the first time (titled `title`), sets the path it shows
        from now on (`tree`: the message added at its end, or a new version,
        versions.py), puts `message` at the path's end (None: it is there
        already, to be answered again), and sets the run. `before`: the
        conversation as the caller read it: False if it changed meanwhile,
        or while an earlier run, started after `stale_before`, is still
        answering. `expires_at` None: the conversation is kept.
        """
        path, branches = tree.dump()
        names = {"#thread": "thread_id", "#title": "title", "#source": "title_source", "#created": "created_at",
                 "#updated": "updated_at", "#expires": "expires_at", "#agents": "agents", "#run": "run",
                 "#question": "question_id", "#count": "message_count", "#path": "path", "#branches": "branches",
                 "#rev": "rev", "#heads": "heads"}
        values = {":thread": thread_id, ":title": title, ":message": "message", ":now": now,
                  ":list": _list_pk(tenant_id, user_id), ":agents": {run.agent}, ":run": run.model_dump(),
                  ":question": run.message_id, ":stale": stale_before, ":count": tree.count, ":path": path,
                  ":branches": branches, ":rev": (before.rev or 0) + 1 if before else 1, ":heads": {}}
        expiry, remove = _expiry(expires_at, values)
        update = ("SET #thread = :thread, #title = if_not_exists(#title, :title), "
                  "#source = if_not_exists(#source, :message), #created = if_not_exists(#created, :now), "
                  f"#updated = :now{expiry}, gsi1pk = :list, gsi1sk = :now, #run = :run, #question = :question, "
                  "#count = :count, #path = :path, #branches = :branches, #rev = :rev, "
                  f"#heads = if_not_exists(#heads, :heads){remove} ADD #agents :agents")
        condition = f"(attribute_not_exists(#run) OR #run.since < :stale) AND {_unchanged(before, values)}"
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
        if message is not None:
            self._put_messages(tenant_id, user_id, thread_id, [message], tree.node(tree.count), run.agent)
        return True

    def add_answer(self, tenant_id: str, user_id: str, thread_id: str, run_id: str, messages: list[dict], *,
                   agent: str, now: str, expires_at: int | None, checkpointed: bool = False) -> bool:
        """
        After the run: appends what it streamed (maybe nothing) to the end of
        the path, as `agent`'s, then removes the run, in that order, so
        whoever finds it answered finds the whole answer. `checkpointed`: the
        agent's checkpoint has the conversation as it is now (Conversation.heads).
        An answer asked for again that came to nothing leaves the one before
        it shown (Tree.without_empty_tail). False if the conversation no
        longer exists (deleted meanwhile), or the run isn't its run.
        """
        key = {"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"}
        item = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
        if item is None:
            return False
        # What it shows doesn't change while the run answers (one at a time): only this run adds to it.
        conversation = _conversation(item)
        tree, rev = conversation.tree(), (conversation.rev or 0) + 1
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
                self._put_messages(tenant_id, user_id, thread_id, messages,
                                   (tree.path[-1][0], int(result["Attributes"]["message_count"])), agent)
            names, values = {"#run": "run", "#rev": "rev"}, {":run": run_id, ":rev": rev}
            update = "REMOVE #run SET #rev = :rev"
            if checkpointed:
                names["#heads"], values[":heads"] = "heads", {**conversation.heads, agent: rev}
                update += ", #heads = :heads"
            if not messages and tree.empty_tail:
                shown = tree.without_empty_tail()
                path, branches = shown.dump()
                names |= {"#path": "path", "#branches": "branches", "#count": "message_count",
                          "#question": "question_id"}
                values |= {":path": path, ":branches": branches, ":count": shown.count,
                           ":question": self.newest_question(tenant_id, user_id, thread_id, shown)}
                update += ", #path = :path, #branches = :branches, #count = :count, #question = :question"
            self.table.update_item(Key=key, UpdateExpression=update, ConditionExpression="#run.run_id = :run",
                                   ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        return True

    def show(self, tenant_id: str, user_id: str, thread_id: str, tree: Tree, *, before: Conversation,
             stale_before: str) -> bool:
        """
        Shows another path through the versions (`tree`). False if the
        conversation changed since `before` was read, or while a run, started
        after `stale_before`, is answering.
        """
        path, branches = tree.dump()
        values = {":path": path, ":branches": branches, ":count": tree.count, ":rev": (before.rev or 0) + 1,
                  ":question": self.newest_question(tenant_id, user_id, thread_id, tree), ":stale": stale_before}
        try:
            self.table.update_item(
                Key={"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": "META"},
                UpdateExpression="SET #path = :path, #branches = :branches, #count = :count, #rev = :rev, "
                                 "#question = :question",
                ConditionExpression=f"(attribute_not_exists(#run) OR #run.since < :stale) "
                                    f"AND {_unchanged(before, values)}",
                ExpressionAttributeNames={"#path": "path", "#branches": "branches", "#count": "message_count",
                                          "#rev": "rev", "#question": "question_id", "#run": "run"},
                ExpressionAttributeValues=values,
            )
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

    def _put_messages(self, tenant_id: str, user_id: str, thread_id: str, messages: list[dict], last: Node,
                      agent: str) -> None:
        """
        Numbers the messages so that the last one is at `last` (its branch,
        and the path's new message_count), each with the agent it was said to
        or by. No TTL of their own: they go with the conversation.
        """
        pk = _conversation_pk(tenant_id, user_id, thread_id)
        branch, position = last
        with self.table.batch_writer() as batch:
            for n, message in enumerate(messages, start=position - len(messages) + 1):
                batch.put_item(Item={"pk": pk, "sk": _message_sk(n, branch), "message": message, "agent": agent})

    def find_message(self, tenant_id: str, user_id: str, thread_id: str, message_id: str,
                     tree: Tree) -> Found | None:
        """
        The message with this (AG-UI) id on the path the conversation shows,
        or None. Looks from the newest back, since messages edited or rated
        are mostly recent.
        """
        projection = {"ProjectionExpression": "sk, #m.#id, #m.#role",
                      "ExpressionAttributeNames": {"#m": "message", "#id": "id", "#role": "role"}}
        for position, item in self._path(tenant_id, user_id, thread_id, tree, 1, tree.count, newest_first=True,
                                         **projection):
            if item["message"].get("id") == message_id:
                return Found(position, item["message"].get("role"))
        return None

    def newest_question(self, tenant_id: str, user_id: str, thread_id: str, tree: Tree) -> str | None:
        """The id of the newest user message on the path: the one the same id sent again answers again."""
        projection = {"ProjectionExpression": "sk, #m.#id, #m.#role",
                      "ExpressionAttributeNames": {"#m": "message", "#id": "id", "#role": "role"}}
        for _, item in self._path(tenant_id, user_id, thread_id, tree, 1, tree.count, newest_first=True, **projection):
            if item["message"].get("role") == "user":
                return item["message"].get("id")
        return None

    def message(self, tenant_id: str, user_id: str, thread_id: str, node: Node) -> dict | None:
        """The message stored at `node` (its branch and position), or None."""
        item = self.table.get_item(Key={"pk": _conversation_pk(tenant_id, user_id, thread_id),
                                        "sk": _message_sk(node[1], node[0])}, ConsistentRead=True).get("Item")
        return item and _plain(item["message"])

    def rate(self, tenant_id: str, user_id: str, thread_id: str, node: Node, rating: Rating | None) -> bool:
        """Sets (or, with None, clears) the user's rating of the message at `node`. False if it's gone."""
        key = {"pk": _conversation_pk(tenant_id, user_id, thread_id), "sk": _message_sk(node[1], node[0])}
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

    def messages(self, tenant_id: str, user_id: str, thread_id: str, limit: int) -> list[dict]:
        """The latest `limit` messages shown, oldest first: all that were written, even a moment ago."""
        conversation = self.get(tenant_id, user_id, thread_id, expired=True)
        return self.history(tenant_id, user_id, thread_id, conversation.tree(), limit)[0] if conversation else []

    def history(self, tenant_id: str, user_id: str, thread_id: str, tree: Tree, limit: int,
                before: int | None = None) -> tuple[list[dict], dict[str, Rating], list[int]]:
        """
        The latest `limit` messages of the path `tree` shows, oldest first,
        the user's ratings of them by message id, and each one's position.
        `before`: only the messages before this position, for the page
        before one already read.
        """
        last = tree.count if before is None else min(tree.count, before - 1)
        found = list(self._path(tenant_id, user_id, thread_id, tree, max(1, last - limit + 1), last))
        return ([_plain(item["message"]) for _, item in found],
                {item["message"]["id"]: item["rating"] for _, item in found if "rating" in item},
                [position for position, _ in found])

    def said(self, tenant_id: str, user_id: str, thread_id: str, tree: Tree,
             limit: int) -> list[tuple[str | None, dict]]:
        """
        The latest `limit` messages of the path `tree` shows, oldest first,
        with the agent each was said to or by (None: saved before messages
        had one).
        """
        return [(item.get("agent"), _plain(item["message"]))
                for _, item in self._path(tenant_id, user_id, thread_id, tree, max(1, tree.count - limit + 1),
                                          tree.count)]

    def _path(self, tenant_id: str, user_id: str, thread_id: str, tree: Tree, first: int, last: int, *,
              newest_first: bool = False, **projection) -> Iterator[tuple[int, dict]]:
        """The path's messages at positions first to last, as (position, item): one query per segment."""
        pk = _conversation_pk(tenant_id, user_id, thread_id)
        segments = tree.segments(first, last)
        for branch, start, end in reversed(segments) if newest_first else segments:
            params = {"KeyConditionExpression": Key("pk").eq(pk)
                      & Key("sk").between(_message_sk(start, branch), _message_sk(end, branch)),
                      "ScanIndexForward": not newest_first, "ConsistentRead": True, **projection}
            while True:
                result = self.table.query(**params)
                for item in result["Items"]:
                    yield _position(item["sk"]), item
                if "LastEvaluatedKey" not in result:
                    break
                params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def list(self, tenant_id: str, user_id: str, limit: int,
             cursor: str | None = None) -> tuple[list[Conversation], str | None]:
        """Newest first. Returns the conversations and a cursor for the next page (None at the end)."""
        params = {
            "IndexName": CONVERSATIONS_BY_USER,
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
        params = {"IndexName": CONVERSATIONS_BY_USER,
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


def _unchanged(before: Conversation | None, values: dict) -> str:
    """
    The condition that the conversation is still as the caller read it
    (`before`; None: there was none), for an update with these values.
    """
    if before is None:
        return "attribute_not_exists(#count)"
    if before.rev is None:   # saved before versions: its message_count says
        values[":before_count"] = before.message_count
        return "attribute_not_exists(#rev) AND #count = :before_count"
    values[":before_rev"] = before.rev
    return "#rev = :before_rev"


def _memory_pk(tenant_id: str, user_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}#MEMORY"


class MemoryRepository:
    """
    What the assistant remembers about each user: a few dozen small items
    under one key, read in one query. The tenant and user come from the
    caller's token.
    """

    def __init__(self, table):
        self.table = table

    def read(self, tenant_id: str, user_id: str) -> tuple[bool, list[Memory]]:
        """Whether the user has memory on, and every memory, oldest first."""
        params = {"KeyConditionExpression": Key("pk").eq(_memory_pk(tenant_id, user_id)), "ConsistentRead": True}
        enabled, memories = True, []
        while True:
            result = self.table.query(**params)
            for item in result["Items"]:
                if item["sk"] == "SETTINGS":
                    enabled = item.get("enabled", True)
                elif item["sk"].startswith("MEM#"):
                    memories.append(Memory.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk")}))
            if "LastEvaluatedKey" not in result:
                return enabled, sorted(memories, key=lambda memory: memory.created_at)
            params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def add(self, tenant_id: str, user_id: str, memory: Memory) -> None:
        values = {f":{k}": v for k, v in memory.model_dump().items() if v is not None}
        self.table.update_item(
            Key={"pk": _memory_pk(tenant_id, user_id), "sk": f"MEM#{memory.memory_id}"},
            UpdateExpression="SET " + ", ".join(f"#{name[1:]} = {name}" for name in values),
            ConditionExpression="attribute_not_exists(pk)",
            ExpressionAttributeNames={f"#{name[1:]}": name[1:] for name in values},
            ExpressionAttributeValues=values,
        )

    def edit(self, tenant_id: str, user_id: str, memory_id: str, text: str, now: str) -> Memory | None:
        """The memory with its new text, or None if there is no such memory."""
        try:
            result = self.table.update_item(
                Key={"pk": _memory_pk(tenant_id, user_id), "sk": f"MEM#{memory_id}"},
                UpdateExpression="SET #text = :text, #updated = :now",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#text": "text", "#updated": "updated_at"},
                ExpressionAttributeValues={":text": text, ":now": now},
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if is_condition_failure(e):
                return None
            raise
        return Memory.model_validate({k: v for k, v in result["Attributes"].items() if k not in ("pk", "sk")})

    def delete(self, tenant_id: str, user_id: str, memory_id: str) -> bool:
        """False if there was no such memory."""
        try:
            self.table.delete_item(Key={"pk": _memory_pk(tenant_id, user_id), "sk": f"MEM#{memory_id}"},
                                   ConditionExpression="attribute_exists(pk)")
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise
        return True

    def delete_all(self, tenant_id: str, user_id: str, settings: bool = False) -> int:
        """Every memory of the user's (and, with `settings`, whether memory was on). Returns how many records."""
        params = {"KeyConditionExpression": Key("pk").eq(_memory_pk(tenant_id, user_id)),
                  "ProjectionExpression": "pk, sk"}
        deleted = 0
        with self.table.batch_writer() as batch:
            while True:
                result = self.table.query(**params)
                for item in result["Items"]:
                    if settings or item["sk"] != "SETTINGS":
                        batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                        deleted += 1
                if "LastEvaluatedKey" not in result:
                    return deleted
                params["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    def set_enabled(self, tenant_id: str, user_id: str, enabled: bool) -> None:
        self.table.update_item(Key={"pk": _memory_pk(tenant_id, user_id), "sk": "SETTINGS"},
                               UpdateExpression="SET #enabled = :enabled",
                               ExpressionAttributeNames={"#enabled": "enabled"},
                               ExpressionAttributeValues={":enabled": enabled})


def _files_pk(tenant_id: str, user_id: str) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}#FILES"


def _named_sk(thread_id: str, name: str) -> str:
    return f"NAMED#{thread_id}#{name.casefold()}"


def _file(item: dict) -> File:
    return File.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk")})


class FileRepository:
    """
    The records of a user's files (the bytes are in S3, files.py), under one
    key per user, newest last. The tenant and user come from the caller's
    token, so a file id from a client only ever names one of their own.
    """

    def __init__(self, table):
        self.table = table

    def create(self, tenant_id: str, user_id: str, file: File) -> None:
        values = {f":{k}": v for k, v in file.model_dump().items() if v is not None}
        self.table.update_item(
            Key={"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file.file_id}"},
            UpdateExpression="SET " + ", ".join(f"#{name[1:]} = {name}" for name in values),
            ConditionExpression="attribute_not_exists(pk)",
            ExpressionAttributeNames={f"#{name[1:]}": name[1:] for name in values},
            ExpressionAttributeValues=values,
        )

    def get(self, tenant_id: str, user_id: str, file_id: str) -> File | None:
        """The file, or None. A pending upload whose day ran out is gone, even before TTL removes it."""
        item = self.table.get_item(Key={"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file_id}"},
                                   ConsistentRead=True).get("Item")
        if not item or "expires_at" in item and item["expires_at"] <= time.time():
            return None
        return _file(item)

    def attach(self, tenant_id: str, user_id: str, file_id: str, thread_id: str, now: str) -> File | None:
        """A pending upload sent with a message: kept from now on, in that conversation. None if it's gone."""
        try:
            result = self.table.update_item(
                Key={"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file_id}"},
                UpdateExpression="SET #status = :ready, #thread = if_not_exists(#thread, :thread), "
                                 "#updated = :now REMOVE #expires",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#status": "status", "#thread": "thread_id", "#updated": "updated_at",
                                          "#expires": "expires_at"},
                ExpressionAttributeValues={":ready": "ready", ":thread": thread_id, ":now": now},
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if is_condition_failure(e):
                return None
            raise
        return _file(result["Attributes"])

    def named(self, tenant_id: str, user_id: str, thread_id: str, name: str) -> str | None:
        """The id of the file an agent saved as `name` in this conversation, if any."""
        item = self.table.get_item(Key={"pk": _files_pk(tenant_id, user_id), "sk": _named_sk(thread_id, name)},
                                   ConsistentRead=True).get("Item")
        return item and item["file_id"]

    def name(self, tenant_id: str, user_id: str, thread_id: str, name: str, file_id: str) -> None:
        """Saved as `name` in this conversation again, it is a new version of this file."""
        self.table.update_item(Key={"pk": _files_pk(tenant_id, user_id), "sk": _named_sk(thread_id, name)},
                               UpdateExpression="SET file_id = :file", ExpressionAttributeValues={":file": file_id})

    def add_version(self, tenant_id: str, user_id: str, file_id: str, version: FileVersion,
                    content_type: str) -> File | None:
        """
        Its new newest version, if it is the one after the newest (another
        save may have taken that number meanwhile): None then, or if it's gone.
        """
        try:
            result = self.table.update_item(
                Key={"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file_id}"},
                UpdateExpression="SET #versions = list_append(#versions, :version), #size = :size, "
                                 "#type = :type, #updated = :now",
                ConditionExpression="attribute_exists(pk) AND size(#versions) = :before",
                ExpressionAttributeNames={"#versions": "versions", "#size": "size", "#type": "content_type",
                                          "#updated": "updated_at"},
                ExpressionAttributeValues={":version": [version.model_dump()], ":size": version.size,
                                           ":type": content_type, ":now": version.created_at,
                                           ":before": version.version - 1},
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if is_condition_failure(e):
                return None
            raise
        return _file(result["Attributes"])

    def list(self, tenant_id: str, user_id: str, limit: int, cursor: str | None = None, *,
             source: str | None = None, kind: str | None = None,
             thread_id: str | None = None) -> tuple[list[File], str | None]:
        """
        Newest first, kept ones only (not uploads still pending), filtered;
        and a cursor for the next page (None at the end). A page may have
        fewer than `limit` when filters skip some.
        """
        condition = Attr("status").eq("ready")
        for field, value in (("source", source), ("kind", kind), ("thread_id", thread_id)):
            if value is not None:
                condition &= Attr(field).eq(value)
        params = {"KeyConditionExpression": Key("pk").eq(_files_pk(tenant_id, user_id))
                  & Key("sk").begins_with("FILE#"),
                  "FilterExpression": condition, "ScanIndexForward": False, "Limit": limit}
        if cursor:
            try:
                (file_id,) = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            except (ValueError, TypeError) as e:
                raise InvalidCursor(cursor) from e
            if not isinstance(file_id, str):
                raise InvalidCursor(cursor)
            params["ExclusiveStartKey"] = {"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file_id}"}
        result = self.table.query(**params)
        last = result.get("LastEvaluatedKey")
        next_cursor = base64.urlsafe_b64encode(json.dumps([last["sk"].removeprefix("FILE#")]).encode()) \
            .decode().rstrip("=") if last else None
        return [_file(item) for item in result["Items"]], next_cursor

    def delete(self, tenant_id: str, user_id: str, file_id: str) -> File | None:
        """Removes the record, and its name in its conversation; returns it (its versions are in S3), or None."""
        try:
            result = self.table.delete_item(Key={"pk": _files_pk(tenant_id, user_id), "sk": f"FILE#{file_id}"},
                                            ConditionExpression="attribute_exists(pk)", ReturnValues="ALL_OLD")
        except ClientError as e:
            if is_condition_failure(e):
                return None
            raise
        file = _file(result["Attributes"])
        if file.source == "assistant" and file.thread_id and self.named(tenant_id, user_id, file.thread_id,
                                                                        file.name) == file_id:
            self.table.delete_item(Key={"pk": _files_pk(tenant_id, user_id), "sk": _named_sk(file.thread_id,
                                                                                               file.name)})
        return file

    def delete_all(self, tenant_id: str, user_id: str) -> int:
        """Every record of the user's files (a removed member's). Returns how many records."""
        params = {"KeyConditionExpression": Key("pk").eq(_files_pk(tenant_id, user_id)),
                  "ProjectionExpression": "pk, sk"}
        deleted = 0
        with self.table.batch_writer() as batch:
            while True:
                result = self.table.query(**params)
                for item in result["Items"]:
                    batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                deleted += len(result["Items"])
                if "LastEvaluatedKey" not in result:
                    return deleted
                params["ExclusiveStartKey"] = result["LastEvaluatedKey"]


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


def earlier_cursor(position: int, tree: Tree) -> str:
    """Where the page before a conversation's messages starts reading back from: before `position`, on this path."""
    return base64.urlsafe_b64encode(json.dumps([position, tree.fingerprint(position)]).encode()).decode().rstrip("=")


def read_earlier_cursor(cursor: str) -> tuple[int, str]:
    """The position an earlier_cursor() names, and the path's fingerprint before it. Raises InvalidCursor."""
    try:
        position, fingerprint = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as e:   # not base64, not JSON, or not a pair
        raise InvalidCursor(cursor) from e
    if type(position) is not int or position < 1 or not isinstance(fingerprint, str):
        raise InvalidCursor(cursor)
    return position, fingerprint


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
