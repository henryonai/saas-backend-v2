"""
Conversations: sending a message, which saves it with the run that will
answer it (the HTTP API), then running it (AgentCore: app.py, runs.py); what
the agent keeps as it answers (ConversationLog, TitleMiddleware); following a
run again after a reload; stopping it; the HTTP API that lists, reopens,
renames and deletes them (handler.py); and the worker that deletes the
agents' checkpoints of them (purge.py).
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import boto3
import pytest
from ag_ui.core import Message
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError
from botocore.exceptions import ClientError
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph_checkpoint_aws import DynamoDBSaver
from pydantic import TypeAdapter

import assistant.app
from assistant.checkpoints import CheckpointPurge
from assistant.context import checkpoint_session
from assistant.handler import handler
from assistant.models import Run, current_month, iso
from assistant.purge import process_batch
from testkit.http import FakeContext, call, claims

THREAD = "11111111-2222-3333-4444-555555555555"   # conftest's SESSION: the threadId is the session
AGUI_MESSAGE = TypeAdapter(Message)


def stored(ctx, user_id="alice", tenant_id="t1", thread_id=THREAD):
    return ctx.conversations.get(tenant_id, user_id, thread_id)


@pytest.fixture
def api(ctx):
    """The HTTP API as the page calls it (conftest gives it ctx's table)."""
    return SimpleNamespace(call=lambda *a, **kw: call(handler, *a, **kw), purges=ctx.purges)


def opened(api, user, thread_id=THREAD) -> dict:
    """GET /assistant/conversations/{threadId}: what a page opening it reads."""
    status, conversation = api.call("GET", f"/assistant/conversations/{thread_id}", user)
    assert status == 200, conversation
    return conversation


def while_a_tool_runs(ctx, action) -> None:
    """Does `action` in the middle of the agent's list_orders call (in the HTTP API the tool calls)."""
    get = ctx.api.get

    def get_meanwhile(authorization, path, params=None):
        action()
        return get(authorization, path, params)

    ctx.api.get = get_meanwhile


def wait_until(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# ---------- sending a message ----------

def test_sending_saves_the_message_and_its_run_before_anything_runs(ctx, api, alice):
    status, body = ctx.send("What did we order?", alice, message_id="m1")

    assert status == 201
    conversation = opened(api, alice)
    assert conversation["run_id"] == body["run_id"]   # a page reloaded now follows this run
    assert conversation["messages"] == [{"id": "m1", "role": "user", "content": "What did we order?"}]
    assert [c["title"] for c in api.call("GET", "/assistant/conversations", alice)[1]["items"]] \
        == ["What did we order?"]
    assert ctx.model_calls() == []   # AgentCore hasn't run it yet


def test_the_run_answers_the_saved_message_then_it_is_over(ctx, api, alice):
    ctx.script(ctx.answer("You have no orders yet."))
    run_id = ctx.post("What did we order?", alice)

    events = ctx.run(run_id, alice)

    assert events[0] == {"type": "RUN_STARTED", "threadId": THREAD, "runId": run_id}
    assert ctx.text(events) == "You have no orders yet."
    conversation = opened(api, alice)
    assert conversation["run_id"] is None
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]


def test_one_answer_at_a_time_in_a_conversation(ctx, alice):
    run_id = ctx.post("Hi", alice)
    status, body = ctx.send("Are you there?", alice)   # another tab
    assert status == 409 and "still answering" in body["message"]

    ctx.script(ctx.answer("Hello!"))
    ctx.run(run_id, alice)
    assert ctx.send("Are you there?", alice)[0] == 201


def test_trying_again_sends_the_same_message_without_saving_it_twice(ctx, api, alice):
    ctx.script()   # the model has no answer: the run fails
    assert ctx.error(ctx.run(ctx.post("Hi", alice, message_id="m1"), alice))["code"] == "INTERNAL_ERROR"
    ctx.script(ctx.answer("Hello!"))

    ctx.run(ctx.post("Hi", alice, message_id="m1"), alice)   # Try again

    assert [(m["role"], m["content"]) for m in opened(api, alice)["messages"]] \
        == [("user", "Hi"), ("assistant", "Hello!")]
    assert ctx.usage.usage.get("t1", current_month()).messages == 2   # each try counts


def test_a_message_the_plan_refuses_keeps_nothing(ctx, alice):
    ctx.set_usage("t1", messages=50, tokens=0)
    assert ctx.send("Hi", alice)[0] == 402
    assert stored(ctx) is None


# ---------- what the agent keeps ----------

def test_the_history_is_what_the_page_showed_as_agui_messages(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [{"order_id": "o1", "item": "Book"}], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("You have one order: a Book."))

    events = ctx.run(ctx.post("What did we order?", alice, message_id="q1"), alice)

    messages = ctx.conversations.messages("t1", "alice", THREAD, 50)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[0] == {"id": "q1", "role": "user", "content": "What did we order?"}   # the client's own id
    [tool_call] = messages[1]["toolCalls"]
    assert tool_call["function"] == {"name": "list_orders", "arguments": json.dumps({"limit": 5})}
    assert messages[2]["toolCallId"] == tool_call["id"] and "Book" in messages[2]["content"]
    assert messages[3]["content"] == ctx.text(events) == "You have one order: a Book."
    # Exactly the events' ids, so a reopened conversation and the live one are the same messages.
    assert messages[3]["id"] == next(e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_START")
    for message in messages:
        AGUI_MESSAGE.validate_python(message)   # what @ag-ui/client reads back


def test_the_model_titles_the_conversation_once_out_of_the_stream_and_its_tokens_count(ctx, alice):
    ctx.title_model.title, ctx.title_model.tokens = '"Last week\'s orders."', (40, 5)
    ctx.script(ctx.answer("You ordered a Book.", input_tokens=100, output_tokens=20))

    events = ctx.chat("What did we order last week?", alice)

    conversation = stored(ctx)
    assert (conversation.title, conversation.title_source) == ("Last week's orders", "model")
    assert "Last week" not in json.dumps(events)   # the title call never reached the user's stream
    [sent] = ctx.title_model.calls
    assert "What did we order last week?" in sent[-1].content and "You ordered a Book." in sent[-1].content
    assert ctx.usage.usage.get("t1", conversation.created_at[:7]).tokens == 120 + 45

    ctx.script(ctx.answer("Anything else?"))
    ctx.chat("Thanks", alice)
    assert len(ctx.title_model.calls) == 1   # only after the first answer


def test_the_first_message_stays_the_title_when_the_model_fails(ctx, alice):
    ctx.title_model.fail = True
    ctx.script(ctx.answer("Sure."))
    long_question = "Please   compare " + "our orders " * 20

    events = ctx.chat(long_question, alice)

    assert events[-1]["type"] == "RUN_FINISHED"   # the answer is unaffected
    conversation = stored(ctx)
    assert conversation.title_source == "message"
    assert conversation.title.startswith("Please compare our orders") and conversation.title.endswith("…")
    assert len(conversation.title) == 80


def test_the_model_never_replaces_a_title_the_user_chose(ctx, alice):
    ctx.script(ctx.answer("Sure."))
    ctx.title_model.fail = True
    ctx.chat("Hi", alice)
    ctx.conversations.set_title("t1", "alice", THREAD, "Mine", source="user")

    run = SimpleNamespace(user=SimpleNamespace(require_tenant=lambda: "t1", user_id="alice"), session_id=THREAD)
    assert ctx.history.name(run, "The model's") is False
    assert (stored(ctx).title, stored(ctx).title_source) == ("Mine", "user")


def test_a_failed_run_keeps_the_question(ctx, alice):
    ctx.script()   # the model has no answer: the agent fails

    events = ctx.chat("Hi", alice)

    assert ctx.error(events)["code"] == "INTERNAL_ERROR"
    assert [m["content"] for m in ctx.conversations.messages("t1", "alice", THREAD, 50)] == ["Hi"]
    assert stored(ctx).title == "Hi"
    assert stored(ctx).run is None   # nothing more is coming


def test_while_the_agent_answers_the_conversation_names_its_run(ctx, api, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("No orders yet."))
    midway = []
    while_a_tool_runs(ctx, lambda: midway.append(opened(api, alice)))
    run_id = ctx.post("What did we order?", alice)

    ctx.run(run_id, alice)

    assert midway[0]["run_id"] == run_id
    assert [(m["role"], m["content"]) for m in midway[0]["messages"]] == [("user", "What did we order?")]
    after = opened(api, alice)
    assert after["run_id"] is None and [m["role"] for m in after["messages"]] == ["user", "assistant", "tool",
                                                                                "assistant"]


def test_an_answer_that_ends_after_a_delete_does_not_bring_the_conversation_back(ctx):
    run = start(ctx, THREAD, "2026-10-24T08:00:00.000Z")
    ctx.conversations.delete("t1", "alice", THREAD)

    for answer in ([{"id": "a1", "role": "assistant", "content": "x"}], []):
        assert ctx.conversations.add_answer("t1", "alice", THREAD, run.run_id, answer,
                                            now="2026-10-24T08:00:01.000Z",
                                            expires_at=int(time.time()) + 3600) is False
    assert ctx.table.scan()["Items"] == []


# ---------- following a run: a reload, another tab ----------

def test_a_reload_after_the_answer_reads_the_whole_run_again(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("No orders yet."))
    run_id = ctx.post("What did we order?", alice)
    first = ctx.run(run_id, alice)

    # The same events, ids included: the page draws the same messages. Answered once.
    assert ctx.run(run_id, alice) == first
    assert len(ctx.model_calls()) == 2


def test_a_reload_mid_answer_reads_what_was_said_then_the_rest_live(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("No orders yet."))
    in_tool, release = threading.Event(), threading.Event()
    while_a_tool_runs(ctx, lambda: (in_tool.set(), release.wait(5)))
    run_id = ctx.post("What did we order?", alice)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(ctx.run, run_id, alice)
        assert in_tool.wait(5)
        second = pool.submit(ctx.run, run_id, alice)   # the reloaded page: the same run, mid-answer
        log = ctx.runs.get(run_id, "t1:alice")
        wait_until(lambda: log.readers == 2)
        release.set()
        assert second.result(5) == first.result(5)
    assert ctx.text(first.result()) == "No orders yet."
    assert len(ctx.model_calls()) == 2   # one run, read twice


def test_only_its_user_follows_a_run(ctx, alice):
    ctx.script(ctx.answer("Hello!"))
    run_id = ctx.post("Hi", alice)
    ctx.run(run_id, alice)

    for intruder in (claims("bob", "t1", "member"), claims("alice", "t2", "owner")):
        assert ctx.error(ctx.run(run_id, intruder))["code"] == "NOT_FOUND"


def test_a_run_never_sent_is_not_found(ctx, alice):
    events = ctx.run(str(uuid4()), alice)
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"] and ctx.error(events)["code"] == "NOT_FOUND"


def test_the_session_is_busy_while_a_run_goes_even_with_nobody_reading(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("No orders yet."))
    pings = []
    while_a_tool_runs(ctx, lambda: pings.append(ctx.runtime.get("/ping").json()["status"]))

    ctx.chat("What did we order?", alice)

    assert pings == ["HealthyBusy"] and ctx.runtime.get("/ping").json()["status"] == "Healthy"


# ---------- stopping ----------

def stop(api, user, run_id, thread_id=THREAD):
    return api.call("POST", f"/assistant/conversations/{thread_id}/runs/{run_id}/cancel", user)[0]


def test_stop_ends_the_answer_and_keeps_what_it_wrote(ctx, api, alice, monkeypatch):
    monkeypatch.setattr(assistant.app, "STOP_CHECK_SECONDS", 0)
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {}), ctx.answer("never sent"))
    run_id = ctx.post("What did we order?", alice)
    while_a_tool_runs(ctx, lambda: stop(api, alice, run_id))

    events = ctx.run(run_id, alice)

    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["result"] == {"agent": "assistant", "stopped": True}
    assert len(ctx.model_calls()) == 1   # the answer after the tool was never asked for
    conversation = opened(api, alice)
    assert conversation["run_id"] is None
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant", "tool"]
    assert stop(api, alice, run_id) == 204   # a Stop that comes late is not wrong


def test_deleting_the_conversation_mid_answer_stops_it(ctx, api, alice, monkeypatch):
    monkeypatch.setattr(assistant.app, "STOP_CHECK_SECONDS", 0)
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {}), ctx.answer("never sent"))
    while_a_tool_runs(ctx, lambda: api.call("DELETE", f"/assistant/conversations/{THREAD}", alice))

    events = ctx.chat("What did we order?", alice)

    assert events[-1]["result"]["stopped"] is True and len(ctx.model_calls()) == 1
    assert not [i for i in ctx.table.scan()["Items"] if "#CONV" in i["pk"]]


# ---------- the HTTP API ----------

def start(ctx, thread_id: str, now: str, user_id="alice", tenant_id="t1", expires_in=3600, title="Hi") -> Run:
    """A conversation as sending its first message leaves it: the message, and its run still to answer."""
    run = Run(run_id=str(uuid4()), message_id=str(uuid4()), prompt=title, agent="assistant", since=now)
    ctx.conversations.add_question(tenant_id, user_id, thread_id, run=run, title=title, append=True,
                                   message={"id": run.message_id, "role": "user", "content": title}, now=now,
                                   expires_at=int(time.time()) + expires_in, stale_before="9999")
    return run


def thread(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def test_the_list_is_newest_first_in_pages(ctx, api, alice):
    for n, minute in [(1, 1), (2, 3), (3, 2)]:
        start(ctx, thread(n), f"2026-10-24T08:0{minute}:00.000Z", title=f"Chat {n}")

    status, first = api.call("GET", "/assistant/conversations", alice, query={"limit": "2"})
    assert status == 200 and [c["title"] for c in first["items"]] == ["Chat 2", "Chat 3"]
    status, rest = api.call("GET", "/assistant/conversations", alice,
                            query={"limit": "2", "cursor": first["next_cursor"]})
    assert [c["title"] for c in rest["items"]] == ["Chat 1"] and rest["next_cursor"] is None
    assert first["items"][0] == {"thread_id": thread(2), "title": "Chat 2", "created_at": "2026-10-24T08:03:00.000Z",
                                 "updated_at": "2026-10-24T08:03:00.000Z", "message_count": 1}


def test_a_new_message_moves_the_conversation_to_the_top(ctx, api, alice):
    older = start(ctx, thread(1), "2026-10-24T08:01:00.000Z", title="Older")
    start(ctx, thread(2), "2026-10-24T08:02:00.000Z", title="Newer")
    ctx.conversations.add_answer("t1", "alice", thread(1), older.run_id,
                                 [{"id": "a", "role": "assistant", "content": "x"}],
                                 now="2026-10-24T08:03:00.000Z", expires_at=int(time.time()) + 3600)

    assert [c["title"] for c in api.call("GET", "/assistant/conversations", alice)[1]["items"]] == ["Older", "Newer"]


def test_opening_a_conversation_returns_its_messages(ctx, api, alice):
    ctx.script(ctx.answer("Hello Alice!"))
    ctx.chat("Hi", alice)

    conversation = opened(api, alice)

    assert conversation["title"] == "Test conversation" and conversation["message_count"] == 2
    assert conversation["run_id"] is None
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [("user", "Hi"),
                                                                             ("assistant", "Hello Alice!")]


def test_a_run_that_never_ended_stops_counting_after_an_hour(ctx, api, alice):
    # Its runtime stopped mid-answer (AgentCore ends a stream at 60 minutes) before it could say so.
    for minutes, active in [(59, True), (61, False)]:
        run = start(ctx, THREAD, iso(datetime.now(UTC) - timedelta(minutes=minutes)))
        assert opened(api, alice)["run_id"] == (run.run_id if active else None)
    assert ctx.send("Next question", alice)[0] == 201   # and it doesn't block the next one


def test_rename(ctx, api, alice):
    start(ctx, THREAD, "2026-10-24T08:00:00.000Z")

    status, renamed = api.call("PATCH", f"/assistant/conversations/{THREAD}", alice, body={"title": "  Q3   orders "})

    assert status == 200 and renamed["title"] == "Q3 orders"
    assert stored(ctx).title_source == "user"
    assert api.call("PATCH", f"/assistant/conversations/{THREAD}", alice, body={"title": "   "})[0] == 400


def test_delete_removes_it_at_once_and_queues_the_agents_copy(ctx, api, alice):
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)

    assert api.call("DELETE", f"/assistant/conversations/{THREAD}", alice)[0] == 204

    assert api.call("GET", f"/assistant/conversations/{THREAD}", alice)[0] == 404
    assert api.call("GET", "/assistant/conversations", alice)[1]["items"] == []
    assert not [i for i in ctx.table.scan()["Items"] if "#CONV" in i["pk"]]
    # The agent's checkpoints of it: its thread for this user.
    assert api.purges == [[checkpoint_session("t1:alice", THREAD, "assistant")]]


def test_nobody_reaches_another_users_conversation(ctx, api):
    run = start(ctx, THREAD, iso(datetime.now(UTC)))
    for intruder in (claims("bob", "t1", "member"), claims("alice", "t2", "owner")):
        assert api.call("GET", "/assistant/conversations", intruder)[1]["items"] == []
        assert api.call("GET", f"/assistant/conversations/{THREAD}", intruder)[0] == 404
        assert api.call("PATCH", f"/assistant/conversations/{THREAD}", intruder, body={"title": "x"})[0] == 404
        assert api.call("DELETE", f"/assistant/conversations/{THREAD}", intruder)[0] == 404
        assert stop(api, intruder, run.run_id) == 404
    assert stored(ctx).title == "Hi" and not stored(ctx).run.cancelled and api.purges == []


def test_expired_conversations_are_gone_before_ttl_removes_them(ctx, api, alice):
    start(ctx, THREAD, "2026-10-24T08:00:00.000Z", expires_in=-60)
    assert api.call("GET", "/assistant/conversations", alice)[1]["items"] == []
    assert api.call("GET", f"/assistant/conversations/{THREAD}", alice)[0] == 404


def test_bad_input(api, alice):
    assert api.call("GET", "/assistant/conversations", alice, query={"cursor": "not-a-cursor"})[0] == 400
    assert api.call("GET", "/assistant/conversations/short", alice)[0] == 404
    assert api.call("GET", "/assistant/conversations", claims("alice"))[0] == 403   # no workspace yet


# ---------- the purge worker ----------

CHECKPOINTS = "checkpoints-test"   # the table and the bucket


@pytest.fixture
def checkpoints():
    """
    The checkpoint table and bucket as template.yaml makes them, written by
    the runtime's checkpointer (container.py) through a small graph, and the
    purge worker's CheckpointPurge.
    """
    boto3.client("dynamodb").create_table(
        TableName=CHECKPOINTS, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                              {"AttributeName": "SK", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=CHECKPOINTS, CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"})
    saver = DynamoDBSaver(table_name=CHECKPOINTS, region_name="ap-southeast-1", ttl_seconds=30 * 86400,
                          enable_checkpoint_compression=True, s3_offload_config={"bucket_name": CHECKPOINTS})
    builder = StateGraph(MessagesState)
    builder.add_node("answer", lambda state: {"messages": [AIMessage(f"Answer {len(state['messages'])}")]})
    builder.add_edge(START, "answer")
    graph = builder.compile(checkpointer=saver)
    table = boto3.resource("dynamodb").Table(CHECKPOINTS)

    def converse(thread: str, *questions: str) -> None:
        for question in questions:
            graph.invoke({"messages": [HumanMessage(question)]}, {"configurable": {"thread_id": thread}})

    def left(thread: str) -> dict:
        """What is still stored for this thread: records by kind, and S3 objects."""
        kinds = {}
        for item in table.scan()["Items"]:
            if thread in item["PK"]:
                kind = item["PK"].split("_", 1)[0]
                kinds[kind] = kinds.get(kind, 0) + 1
        objects = s3.list_objects_v2(Bucket=CHECKPOINTS, Prefix=f"{thread}/").get("KeyCount", 0)
        return {**kinds, **({"S3": objects} if objects else {})}

    return SimpleNamespace(saver=saver, table=table, s3=s3, converse=converse, left=left,
                           purge=CheckpointPurge(table, s3, CHECKPOINTS))


def sqs(*bodies: dict) -> dict:
    return {"Records": [{"messageId": f"m{n}", "receiptHandle": f"r{n}", "body": json.dumps(body),
                         "attributes": {}, "messageAttributes": {}, "md5OfBody": "", "eventSource": "aws:sqs",
                         "awsRegion": "ap-southeast-1", "eventSourceARN": "arn:aws:sqs:ap-southeast-1:1:q"}
                        for n, body in enumerate(bodies, start=1)]}


# Incompressible, so its checkpoint is over 350 KB even compressed: DynamoDBSaver puts it in S3.
HUGE = base64.b64encode(os.urandom(450_000)).decode()


def test_the_purge_deletes_every_checkpoint_record_of_the_conversation_and_nothing_else(checkpoints):
    mine = checkpoint_session("t1:alice", THREAD, "assistant")
    another = checkpoint_session("t1:bob", THREAD, "assistant")
    checkpoints.converse(mine, "Hi", HUGE, "Thanks")
    checkpoints.converse(another, "Hello")
    # Every kind of record DynamoDBSaver writes, so the purge meets them all.
    assert set(checkpoints.left(mine)) == {"CHECKPOINT", "WRITES", "CHUNK", "S3"}
    kept = checkpoints.left(another)

    result = process_batch(sqs({"threads": [mine]}), FakeContext(), checkpoints.purge)

    assert result == {"batchItemFailures": []}
    assert checkpoints.left(mine) == {}
    assert checkpoints.left(another) == kept
    assert checkpoints.saver.get_tuple({"configurable": {"thread_id": another}}) is not None


class FlakyTable:
    """The checkpoint table, failing as a throttled DynamoDB does once `deletes` records are deleted."""

    def __init__(self, table, deletes: int):
        self.table = table
        self.deletes: int | None = deletes

    def query(self, **params):
        return self.table.query(**params)

    @contextmanager
    def batch_writer(self):
        with self.table.batch_writer() as batch:
            def delete_item(Key):
                if self.deletes == 0:
                    raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "BatchWriteItem")
                if self.deletes is not None:
                    self.deletes -= 1
                batch.delete_item(Key=Key)
            yield SimpleNamespace(delete_item=delete_item)


def test_a_failed_purge_is_retried_and_finishes_the_job(checkpoints):
    mine = checkpoint_session("t1:alice", THREAD, "assistant")
    checkpoints.converse(mine, "Hi", "Thanks")
    stored = checkpoints.left(mine)
    # Fails as many deletes in as deleting every payload and checkpoint would
    # take: deleted before the writes, the checkpoints would leave writes
    # nothing leads to.
    flaky = FlakyTable(checkpoints.table, deletes=stored["CHUNK"] + stored["CHECKPOINT"])
    message = sqs({"threads": [mine]})

    with pytest.raises(BatchProcessingError):   # the whole batch (its one message) failed: SQS retries it
        process_batch(message, FakeContext(), CheckpointPurge(flaky, checkpoints.s3, CHECKPOINTS))
    # The checkpoints go last, so what is left can still be found.
    assert checkpoints.left(mine)["CHECKPOINT"] > 0
    flaky.deletes = None
    assert process_batch(message, FakeContext(), CheckpointPurge(flaky, checkpoints.s3, CHECKPOINTS)) == {
        "batchItemFailures": []}
    assert checkpoints.left(mine) == {}


# ---------- the Lambdas stay light ----------

def test_the_lambdas_never_import_the_agents_dependencies():
    # They get the shared layer, not LangChain, the AgentCore SDK, AG-UI or httpx (the Runtime's zip has those).
    heavy = ("langchain", "langchain_core", "langgraph", "langchain_aws", "langchain_openai", "ag_ui",
             "bedrock_agentcore", "httpx", "yaml", "openai")
    script = ("import sys, assistant.handler, assistant.purge; "
              f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({heavy!r})))")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
