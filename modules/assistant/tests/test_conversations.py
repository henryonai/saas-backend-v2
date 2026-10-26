"""
Conversations: sending a message, which saves it with the run that will
answer it (the HTTP API), then running it (AgentCore: app.py, runs.py); what
the agent keeps as it answers (ConversationLog, TitleMiddleware); following a
run again after a reload; stopping it; the HTTP API that lists, reopens,
renames and deletes them (handler.py); how long they are kept, and an agent
that lost its checkpoints; and the worker that deletes the agents'
checkpoints of them, and a removed member's conversations (purge.py).
"""
import base64
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import boto3
import pytest
import yaml
from ag_ui.core import Message
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph import START, MessagesState, StateGraph
from langgraph_checkpoint_aws import DynamoDBSaver
from pydantic import TypeAdapter

import assistant.app
import assistant.purge
from assistant.agui import agent_messages
from assistant.app import Streamed, keep_what_the_user_saw
from assistant.checkpoints import CheckpointPurge
from assistant.context import AgentContext, checkpoint_session
from assistant.factory import thread_config
from assistant.handler import handler
from assistant.middleware import INTERRUPTED
from assistant.models import Run, current_month, iso
from assistant.purge import process_batch
from assistant.versions import Tree
from shared.auth import user_from_claims
from testkit.http import FakeContext, call, claims
from testkit.logs import captured_logs

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

    assert (events[0]["type"], events[0]["threadId"], events[0]["runId"]) == ("RUN_STARTED", THREAD, run_id)
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


def test_each_user_has_an_hourly_limit_on_messages(ctx, alice):
    now = datetime.now(UTC)
    for hour in (now, now + timedelta(hours=1)):   # whichever hour the request lands in
        ctx.table.put_item(Item={"pk": "TENANT#t1", "sk": f"USER#alice#HOUR#{hour:%Y-%m-%dT%H}", "messages": 20})

    status, body = ctx.send("Hi", alice)

    assert status == 429 and body["message"].startswith("You can send the assistant 20 messages an hour. Try again in")
    assert ctx.usage.usage.get("t1", current_month()).messages == 0   # the month's allowance isn't touched
    assert stored(ctx) is None
    assert ctx.send("Hi", claims("bob", "t1", "member"))[0] == 201   # each user has their own
    counted = ctx.table.get_item(Key={"pk": "TENANT#t1", "sk": f"USER#bob#HOUR#{datetime.now(UTC):%Y-%m-%dT%H}"})
    assert counted["Item"]["messages"] == 1 and counted["Item"]["expires_at"] < time.time() + 2 * 3600


# ---------- editing a message, asking for an answer again ----------

def history_of(api, user) -> list[tuple[str, str]]:
    return [(m["role"], m.get("content", "")) for m in opened(api, user)["messages"]]


def sent_to_model(ctx, call: int = 0) -> list[tuple[str, str]]:
    return [(m.type, m.text) for m in ctx.model_calls()[call] if m.type != "system"]


def show_version(api, user, message_id: str, version: int) -> tuple[int, dict]:
    """POST .../messages/{id}/version: "‹ 1/2 ›" on a message."""
    return api.call("POST", f"/assistant/conversations/{THREAD}/messages/{message_id}/version", user,
                    body={"version": version})


def france_and_germany(ctx, alice) -> None:
    """q1 Capital of France? Paris. q2 And Germany? Berlin."""
    ctx.script(ctx.answer("Paris."))
    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)
    ctx.script(ctx.answer("Berlin."))
    ctx.run(ctx.post("And Germany?", alice, message_id="q2"), alice)


FRANCE_AND_GERMANY = [("user", "Capital of France?"), ("assistant", "Paris."), ("user", "And Germany?"),
                      ("assistant", "Berlin.")]


def test_editing_a_message_shows_the_new_version_and_keeps_the_old_one(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script(ctx.answer("Rome."))

    ctx.run(ctx.post("Capital of Italy?", alice, message_id="q3", replaces="q1"), alice)

    conversation = opened(api, alice)
    assert history_of(api, alice) == [("user", "Capital of Italy?"), ("assistant", "Rome.")]
    assert conversation["message_count"] == 2 and conversation["versions"] == {"q3": {"index": 2, "count": 2}}
    # The agent went back too: the model was sent the edited message alone.
    assert sent_to_model(ctx) == [("human", "Capital of Italy?")]

    status, first = show_version(api, alice, "q3", 1)

    assert status == 200 and [(m["role"], m["content"]) for m in first["messages"]] == FRANCE_AND_GERMANY
    assert first["versions"] == {"q1": {"index": 1, "count": 2}} and first["message_count"] == 4
    assert history_of(api, alice) == FRANCE_AND_GERMANY   # it stays shown
    ctx.script(ctx.answer("Madrid."))
    ctx.chat("And Spain?", alice)
    # This version goes on: the agent, whose checkpoint is of the other, starts again from its history.
    assert sent_to_model(ctx) == [("human", "Capital of France?"), ("ai", "Paris."), ("human", "And Germany?"),
                                  ("ai", "Berlin."), ("human", "And Spain?")]
    assert history_of(api, alice)[4:] == [("user", "And Spain?"), ("assistant", "Madrid.")]
    second = show_version(api, alice, "q1", 2)[1]   # and the edit is still there
    assert [(m["role"], m["content"]) for m in second["messages"]] == [("user", "Capital of Italy?"),
                                                                       ("assistant", "Rome.")]


def test_asking_for_an_answer_again_shows_the_new_one_and_keeps_the_old_one(ctx, api, alice):
    ctx.script(ctx.answer("Paris."))
    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)
    ctx.script(ctx.answer("It's Paris."))

    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)   # the same message again

    conversation = opened(api, alice)
    newer = conversation["messages"][1]["id"]
    assert history_of(api, alice) == [("user", "Capital of France?"), ("assistant", "It's Paris.")]
    assert conversation["versions"] == {newer: {"index": 2, "count": 2}}
    assert sent_to_model(ctx) == [("human", "Capital of France?")]   # not the old answer, not the question twice
    first = show_version(api, alice, newer, 1)[1]
    assert [m["content"] for m in first["messages"]] == ["Capital of France?", "Paris."]
    assert first["versions"] == {first["messages"][1]["id"]: {"index": 1, "count": 2}}


def test_any_answer_can_be_asked_for_again(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script(ctx.answer("Paris, of course."))

    ctx.run(ctx.post("Capital of France?", alice, message_id="q1", replaces="q1"), alice)

    assert history_of(api, alice) == [("user", "Capital of France?"), ("assistant", "Paris, of course.")]
    assert sent_to_model(ctx) == [("human", "Capital of France?")]
    older = show_version(api, alice, opened(api, alice)["messages"][1]["id"], 1)[1]
    assert [(m["role"], m["content"]) for m in older["messages"]] == FRANCE_AND_GERMANY


def test_an_answer_asked_for_again_that_never_came_leaves_the_old_one(ctx, api, alice):
    ctx.script(ctx.answer("Paris."))
    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)
    ctx.script()   # the model has no answer: the run fails before it says anything

    assert ctx.error(ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice))["code"] \
        == "INTERNAL_ERROR"

    conversation = opened(api, alice)
    assert history_of(api, alice) == [("user", "Capital of France?"), ("assistant", "Paris.")]
    assert conversation["versions"] == {} and conversation["run_id"] is None


def test_after_an_answer_asked_for_again_never_came_the_agent_goes_on_from_what_the_user_sees(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script()   # Berlin asked for again: the run fails before it says anything

    assert ctx.error(ctx.run(ctx.post("And Germany?", alice, message_id="q2", replaces="q2"), alice))["code"] \
        == "INTERNAL_ERROR"
    ctx.script(ctx.answer("Madrid."))
    ctx.chat("And Spain?", alice)

    # Whatever the failed run checkpointed (the question, rewound to), the agent has what is shown: Berlin, once.
    assert sent_to_model(ctx) == [("human", "Capital of France?"), ("ai", "Paris."), ("human", "And Germany?"),
                                  ("ai", "Berlin."), ("human", "And Spain?")]
    assert history_of(api, alice) == [*FRANCE_AND_GERMANY, ("user", "And Spain?"), ("assistant", "Madrid.")]


def test_showing_a_version_takes_one_the_conversation_has(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script(ctx.answer("Rome."))
    ctx.run(ctx.post("Capital of Italy?", alice, message_id="q3", replaces="q1"), alice)

    assert show_version(api, alice, "q3", 3)[0] == 404
    assert show_version(api, alice, "q1", 1)[0] == 404   # not shown: its version 2 is
    assert show_version(api, claims("bob", "t1", "member"), "q3", 1)[0] == 404
    assert show_version(api, alice, "q3", 0)[0] == 422
    assert show_version(api, alice, "q3", 2)[1]["versions"] == {"q3": {"index": 2, "count": 2}}   # the one shown
    ctx.post("And Spain?", alice)
    status, body = show_version(api, alice, "q3", 1)
    assert status == 409 and "still answering" in body["message"]   # while an answer is coming


def test_versions_of_a_version(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script(ctx.answer("Rome."))
    ctx.run(ctx.post("Capital of Italy?", alice, message_id="q3", replaces="q1"), alice)
    ctx.script(ctx.answer("Vienna."))
    ctx.run(ctx.post("And Austria?", alice, message_id="q4"), alice)
    ctx.script(ctx.answer("Bern."))
    ctx.run(ctx.post("And Switzerland?", alice, message_id="q5", replaces="q4"), alice)

    assert opened(api, alice)["versions"] == {"q3": {"index": 2, "count": 2}, "q5": {"index": 2, "count": 2}}
    show_version(api, alice, "q3", 1)
    # Back to the edit: it shows its newest versions on from there.
    assert [m["content"] for m in show_version(api, alice, "q1", 2)[1]["messages"]][::2] \
        == ["Capital of Italy?", "And Switzerland?"]
    assert [m["content"] for m in show_version(api, alice, "q5", 1)[1]["messages"]][::2] \
        == ["Capital of Italy?", "And Austria?"]


def test_a_page_read_back_belongs_to_the_version_shown(ctx, api, alice):
    a_long_conversation(ctx, alice, 250)   # m1 the question, then the answer
    cursor = opened(api, alice)["earlier_cursor"]
    ctx.script(ctx.answer("In short."))
    ctx.run(ctx.post("Tell me briefly", alice, message_id="m0", replaces="m1"), alice)   # another tab edits m1

    status, body = earlier(api, alice, cursor)

    assert status == 409 and "open it again" in body["message"]


def test_a_conversation_saved_before_versions_gets_them(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.table.update_item(Key={"pk": f"TENANT#t1#USER#alice#CONV#{THREAD}", "sk": "META"},
                          UpdateExpression="REMOVE #path, branches, rev, heads",
                          ExpressionAttributeNames={"#path": "path"})
    assert history_of(api, alice) == FRANCE_AND_GERMANY
    ctx.script(ctx.answer("Madrid."))

    ctx.chat("And Spain?", alice)
    assert len(sent_to_model(ctx)) == 5   # its checkpoint goes on: nothing restored
    ctx.script(ctx.answer("Rome."))
    ctx.run(ctx.post("Capital of Italy?", alice, message_id="q3", replaces="q1"), alice)

    assert opened(api, alice)["versions"] == {"q3": {"index": 2, "count": 2}}
    assert [m["content"] for m in show_version(api, alice, "q3", 1)[1]["messages"]][-2:] == ["And Spain?", "Madrid."]


def test_an_answer_in_a_newer_version_is_rated(ctx, api, alice):
    france_and_germany(ctx, alice)
    ctx.script(ctx.answer("Rome."))
    ctx.run(ctx.post("Capital of Italy?", alice, message_id="q3", replaces="q1"), alice)
    answer = opened(api, alice)["messages"][1]["id"]

    assert api.call("PATCH", f"/assistant/conversations/{THREAD}/messages/{answer}", alice,
                    body={"rating": "up"})[0] == 204

    assert opened(api, alice)["ratings"] == {answer: "up"}
    assert show_version(api, alice, "q3", 1)[1]["ratings"] == {}


def test_only_the_users_own_messages_are_edited_one_answer_at_a_time(ctx, api, alice):
    ctx.script(ctx.answer("Paris."))
    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)
    answer_id = opened(api, alice)["messages"][1]["id"]

    assert ctx.send("Edited", alice, replaces=answer_id)[0] == 404   # not a message of the user's
    assert ctx.send("Edited", alice, replaces="no-such-message")[0] == 404
    assert ctx.send("Edited", claims("bob", "t1", "member"), replaces="q1")[0] == 404
    ctx.post("And Germany?", alice)
    assert ctx.send("Edited", alice, replaces="q1")[0] == 409   # while an answer is coming
    assert history_of(api, alice) == [("user", "Capital of France?"), ("assistant", "Paris."),
                                      ("user", "And Germany?")]


def test_messages_past_the_count_are_not_part_of_the_conversation(ctx, api, alice):
    ctx.script(ctx.answer("Paris."))
    ctx.chat("Capital of France?", alice)
    # What an edit's delete that stopped half way leaves: message_count says it's gone.
    ctx.table.put_item(Item={"pk": f"TENANT#t1#USER#alice#CONV#{THREAD}", "sk": "MSG#000003",
                             "message": {"id": "left", "role": "user", "content": "Left behind"}})

    assert history_of(api, alice) == [("user", "Capital of France?"), ("assistant", "Paris.")]
    assert ctx.send("Edited", alice, replaces="left")[0] == 404
    ctx.script(ctx.answer("Berlin."))
    ctx.chat("And Germany?", alice)   # writes over it
    assert history_of(api, alice)[2:] == [("user", "And Germany?"), ("assistant", "Berlin.")]


def test_a_first_message_sent_again_is_titled_again(ctx, alice):
    ctx.title_model.fail = True
    ctx.script()   # the model has no answer: the run fails
    ctx.run(ctx.post("Hi", alice, message_id="m1"), alice)
    assert stored(ctx).title_source == "message"
    ctx.title_model.fail = False
    ctx.script(ctx.answer("Hello!"))

    ctx.run(ctx.post("Hi", alice, message_id="m1"), alice)   # Try again

    assert (stored(ctx).title, stored(ctx).title_source) == ("Test conversation", "model")


# ---------- rating an answer ----------

def test_rating_an_answer(ctx, api, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {}), ctx.answer("No orders yet."))
    ctx.chat("What did we order?", alice)
    question, *_, answer = [m["id"] for m in opened(api, alice)["messages"]]   # the answer's last message
    path = f"/assistant/conversations/{THREAD}/messages/{answer}"

    assert api.call("PATCH", path, alice, body={"rating": "up"})[0] == 204
    assert opened(api, alice)["ratings"] == {answer: "up"}
    assert api.call("PATCH", path, alice, body={"rating": "down"})[0] == 204
    assert opened(api, alice)["ratings"] == {answer: "down"}
    assert api.call("PATCH", path, alice, body={"rating": None})[0] == 204   # taken back
    assert opened(api, alice)["ratings"] == {}

    assert api.call("PATCH", f"/assistant/conversations/{THREAD}/messages/{question}", alice,
                    body={"rating": "up"})[0] == 404   # only answers
    assert api.call("PATCH", path, claims("bob", "t1", "member"), body={"rating": "up"})[0] == 404
    assert api.call("PATCH", path, alice, body={"rating": "meh"})[0] == 422


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


def test_the_model_titles_a_new_conversation_from_its_first_message_out_of_the_stream(ctx, alice):
    ctx.title_model.title, ctx.title_model.tokens = '"Last week\'s orders."', (40, 5)
    ctx.script(ctx.answer("You ordered a Book.", input_tokens=100, output_tokens=20))

    events = ctx.chat("What did we order last week?", alice)

    conversation = stored(ctx)
    assert (conversation.title, conversation.title_source) == ("Last week's orders", "model")
    assert "Last week" not in json.dumps(events)   # the title call never reached the user's stream
    [sent] = ctx.title_model.calls
    assert sent[-1].content == "What did we order last week?"   # the question: asked while the agent answers
    assert ctx.usage.usage.get("t1", conversation.created_at[:7]).tokens == 120 + 45

    ctx.script(ctx.answer("Anything else?"))
    ctx.chat("Thanks", alice)
    assert len(ctx.title_model.calls) == 1   # only the first message


def test_the_answer_does_not_wait_for_its_title(ctx, alice, monkeypatch):
    monkeypatch.setattr(assistant.app, "TITLE_WAIT_SECONDS", 0.2)
    called, release = threading.Event(), threading.Event()
    invoke = ctx.title_model.invoke
    ctx.title_model.__dict__["invoke"] = lambda *a, **kw: (called.set(), release.wait(5), invoke(*a, **kw))[2]
    ctx.script(ctx.answer("Hello!"))

    started = time.monotonic()
    events = ctx.chat("Hi", alice)

    assert called.is_set() and events[-1]["type"] == "RUN_FINISHED"   # asked for at the start, still writing
    assert time.monotonic() - started < 2
    assert stored(ctx).run is None   # the conversation takes the next message already
    release.set()
    wait_until(lambda: stored(ctx).title == "Test conversation")   # it lands when it's done


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
    assert stored(ctx).title == "Test conversation"   # titled from the question, answered or not
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
        assert ctx.conversations.add_answer("t1", "alice", THREAD, run.run_id, answer, agent="assistant",
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


def client_messages(events: list[dict]) -> list[dict]:
    """
    The messages @ag-ui/client makes of these events, as its reducer does
    (apply/default.ts): a start for a message or tool call it has already
    adds to it rather than making another.
    """
    messages: list[dict] = []

    def by_id(message_id):
        return next((m for m in messages if m["id"] == message_id), None)

    for e in events:
        match e["type"]:
            case "TEXT_MESSAGE_START" if not by_id(e["messageId"]):
                messages.append({"id": e["messageId"], "role": e["role"], "content": ""})
            case "TEXT_MESSAGE_CONTENT":
                by_id(e["messageId"])["content"] += e["delta"]
            case "TOOL_CALL_START" if not any(c["id"] == e["toolCallId"] for m in messages
                                              for c in m.get("toolCalls", [])):
                parent = by_id(e["parentMessageId"]) or messages.append(
                    {"id": e["parentMessageId"], "role": "assistant"}) or by_id(e["parentMessageId"])
                parent.setdefault("toolCalls", []).append({"id": e["toolCallId"], "args": ""})
            case "TOOL_CALL_ARGS":
                next(c for m in messages for c in m.get("toolCalls", []) if c["id"] == e["toolCallId"])["args"] \
                    += e["delta"]
            case "TOOL_CALL_RESULT":
                messages.append({"id": e["messageId"], "role": "tool", "content": e["content"]})
    return messages


def test_a_stream_that_broke_reads_on_from_where_it_was(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("No orders yet."))
    run_id = ctx.post("What did we order?", alice)
    whole = ctx.run(run_id, alice)
    resume = {"startedAt": whole[0]["timestamp"]}

    # Whatever the page had (one event, mid tool call, mid text...), it reads only the rest.
    for after in range(1, len(whole)):
        rest = ctx.run(run_id, alice, resume={**resume, "after": after})
        assert rest[0] == whole[0] and rest[-1] == whole[-1]
        assert len(rest) <= len(whole) - after + 2   # RUN_STARTED, and at most one start that was open
        assert client_messages(whole[:after] + rest) == client_messages(whole), after
    assert len(ctx.model_calls()) == 2   # answered once


def test_a_page_reads_a_run_started_again_from_the_start(ctx, alice):
    ctx.script(ctx.answer("Hello!"))
    run_id = ctx.post("Hi", alice)
    whole = ctx.run(run_id, alice)

    # Another log of the run (a microVM that started it again), or a resume that makes no sense: all of it.
    for resume in ({"after": 3, "startedAt": whole[0]["timestamp"] - 1}, {"after": -1, "startedAt": 1},
                   {"after": "3"}, "nonsense"):
        assert ctx.run(run_id, alice, resume=resume) == whole


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

    # The next message: the agent has the tool's result, once, as the user saw it.
    ctx.script(ctx.answer("Nothing yet."))
    ctx.chat("And now?", alice)
    results = [m for m in ctx.model_calls()[0] if m.type == "tool"]
    assert len(results) == 1 and INTERRUPTED not in results[0].text


def test_stop_ends_the_model_call_and_the_agent_keeps_the_text_so_far(ctx, api, alice, monkeypatch):
    monkeypatch.setattr(assistant.app, "STOP_CHECK_SECONDS", 0)
    story = " ".join(f"word{n}" for n in range(30))
    ctx.script(ctx.answer(story), pause=0.1)   # 3 s to write it all
    run_id = ctx.post("Tell me a story", alice)
    assert stop(api, alice, run_id) == 204     # seen at the first word

    started = time.monotonic()
    events = ctx.run(run_id, alice)

    assert time.monotonic() - started < 1.5    # the model's call ended too, not just the stream
    assert events[-1]["result"] == {"agent": "assistant", "stopped": True}
    said = opened(api, alice)["messages"][-1]["content"]
    assert story.startswith(said) and len(said) < len(story)
    # The next message: the agent has its answer as far as the user saw it.
    ctx.script(ctx.answer("Where was I?"))
    ctx.chat("Go on", alice)
    assert ("ai", said) in [(m.type, m.text) for m in ctx.model_calls()[0]]


def test_a_stopped_answer_still_counts_its_tokens(ctx, api, alice, monkeypatch):
    """
    A stopped model call never gets the model's token counts (they come with
    its last chunk). Its tokens are estimated, about 4 characters a token, and
    count against the plan and in the run's log like any call's: else stopping
    every answer just before its end would use the model for free.
    """
    monkeypatch.setattr(assistant.app, "STOP_CHECK_SECONDS", 0)
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)   # the conversation's title, written beside its first run, is counted then
    before = ctx.usage.usage.get("t1", current_month())
    story = " ".join(f"word{n}" for n in range(30))
    ctx.script(ctx.answer(story, input_tokens=5_000, output_tokens=5_000), pause=0.1)
    run_id = ctx.post("Tell me a story", alice)
    assert stop(api, alice, run_id) == 204

    with captured_logs(assistant.app.logger) as logs:
        events = ctx.run(run_id, alice)

    said = opened(api, alice)["messages"][-1]["content"]
    assert events[-1]["result"]["stopped"] and story.startswith(said) and len(said) < len(story)
    after = ctx.usage.usage.get("t1", current_month())
    sent, wrote = after.input_tokens - before.input_tokens, after.output_tokens - before.output_tokens
    assert sent == count_tokens_approximately(ctx.model_calls()[0])   # what the model was sent
    assert math.ceil(len(said) / 4) <= wrote < math.ceil(len(story) / 4)   # what it wrote before it stopped
    assert after.tokens - before.tokens == sent + wrote
    answered = next(line for line in logs() if line["message"] == "Assistant answered")
    assert (answered["input_tokens"], answered["output_tokens"], answered["stopped"]) == (sent, wrote, True)
    assert events[-1]["usage"] == [{"inputTokens": sent, "outputTokens": wrote, "totalTokens": sent + wrote}]


def test_after_a_stop_the_agent_keeps_what_the_user_saw(ctx, alice):
    # Stop closes the stream mid-step. LangGraph had saved the tool call, but not
    # the result that streamed right after (as on dev), nor the answer's text so far.
    agent = ctx.state.agents["assistant"]
    config = thread_config(AgentContext(user=user_from_claims(alice), authorization="", session_id=ctx.session),
                           "assistant")
    call = {"name": "list_orders", "args": {}, "id": "call-1", "type": "tool_call"}
    agent.update_state(config, {"messages": [HumanMessage("What did we order?"),
                                             AIMessage("", id="ai-1", tool_calls=[call])]}, as_node="model")
    streamed = Streamed()
    for message in (AIMessageChunk("", id="ai-1"), ToolMessage("No orders.", tool_call_id="call-1"),
                    AIMessageChunk("You haven't ", id="ai-2"), AIMessageChunk("ordered", id="ai-2")):
        streamed.add(message)

    keep_what_the_user_saw(agent, config, streamed.messages, {})
    keep_what_the_user_saw(agent, config, streamed.messages, {})   # again: nothing twice

    kept = agent.get_state(config).values["messages"]
    assert [(m.type, m.text) for m in kept] == [("human", "What did we order?"), ("ai", ""), ("tool", "No orders."),
                                                 ("ai", "You haven't ordered")]
    ctx.script(ctx.answer("Shall I look again?"))
    ctx.chat("Go on", alice)
    sent = [(m.type, m.text) for m in ctx.model_calls()[0] if m.type != "system"]
    assert ("tool", "No orders.") in sent and ("ai", "You haven't ordered") in sent
    assert not any(INTERRUPTED in text for _, text in sent)


def test_a_message_is_checkpointed_once_when_its_run_ends(ctx, alice):
    puts = []
    put = ctx.state.checkpointer.put
    ctx.state.checkpointer.put = lambda *args, **kwargs: puts.append(1) or put(*args, **kwargs)
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {}), ctx.answer("None yet."))

    ctx.chat("What did we order?", alice)

    assert len(puts) == 1   # durability="exit": not one per step (here 7)


def test_deleting_the_conversation_mid_answer_stops_it_and_leaves_no_checkpoint(ctx, api, alice, monkeypatch):
    monkeypatch.setattr(assistant.app, "STOP_CHECK_SECONDS", 0)
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)   # an earlier answer: the agent has checkpoints of the conversation
    ctx.script(ctx.tool_call("list_orders", {}), ctx.answer("never sent"))
    while_a_tool_runs(ctx, lambda: api.call("DELETE", f"/assistant/conversations/{THREAD}", alice))

    events = ctx.chat("What did we order?", alice)

    assert events[-1]["result"]["stopped"] is True and len(ctx.model_calls()) == 1
    assert not [i for i in ctx.table.scan()["Items"] if "#CONV" in i["pk"]]
    # The purge was queued at the delete, before this run wrote its checkpoint:
    # the run deleted the thread itself, after it.
    assert api.purges == [[checkpoint_session("t1:alice", THREAD, "assistant")]]
    config = thread_config(AgentContext(user=user_from_claims(alice), authorization="", session_id=THREAD),
                           "assistant")
    assert ctx.state.checkpointer.get_tuple(config) is None


# ---------- the HTTP API ----------

def start(ctx, thread_id: str, now: str, user_id="alice", tenant_id="t1", expires_in=3600, title="Hi") -> Run:
    """A conversation as sending a message leaves it: the message, and its run still to answer."""
    run = Run(run_id=str(uuid4()), message_id=str(uuid4()), prompt=title, agent="assistant", since=now)
    before = ctx.conversations.get(tenant_id, user_id, thread_id, expired=True)
    assert ctx.conversations.add_question(
        tenant_id, user_id, thread_id, run=run, title=title, before=before,
        tree=(before.tree() if before else Tree.load(None, None, 0)).grown(1),
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
                                 [{"id": "a", "role": "assistant", "content": "x"}], agent="assistant",
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


def a_long_conversation(ctx, user: dict, messages: int) -> None:
    """A conversation of `messages` messages, m1 (the user's) to m<messages> (one long answer)."""
    run_id = ctx.post("Tell me everything", user, message_id="m1")
    answer = [{"id": f"m{n}", "role": "assistant", "content": f"Part {n}"} for n in range(2, messages + 1)]
    assert ctx.conversations.add_answer("t1", user["sub"], THREAD, run_id, answer, agent="assistant",
                                        now=iso(datetime.now(UTC)), expires_at=None)


def ids_of(page: dict) -> list[str]:
    return [m["id"] for m in page["messages"]]


def earlier(api, user, cursor: str, limit: int | None = None) -> tuple[int, dict]:
    """GET .../messages?before=: what a page scrolled back to the top of a long conversation reads."""
    query = {"before": cursor, **({"limit": str(limit)} if limit else {})}
    return api.call("GET", f"/assistant/conversations/{THREAD}/messages", user, query=query)


def test_a_long_conversation_opens_at_its_latest_messages_and_reads_back_from_there(ctx, api, alice):
    a_long_conversation(ctx, alice, 450)
    assert api.call("PATCH", f"/assistant/conversations/{THREAD}/messages/m10", alice, body={"rating": "up"})[0] == 204

    conversation = opened(api, alice)
    assert ids_of(conversation) == [f"m{n}" for n in range(251, 451)]
    status, page = earlier(api, alice, conversation["earlier_cursor"], limit=200)
    assert status == 200 and ids_of(page) == [f"m{n}" for n in range(51, 251)]
    status, first = earlier(api, alice, page["earlier_cursor"])   # 100 a page unless asked
    assert ids_of(first) == [f"m{n}" for n in range(1, 51)]
    assert first["earlier_cursor"] is None and first["ratings"] == {"m10": "up"}
    assert conversation["ratings"] == {} and page["ratings"] == {}


def test_reading_back_starts_where_the_page_is_however_the_conversation_grew(ctx, api, alice):
    a_long_conversation(ctx, alice, 210)
    cursor = opened(api, alice)["earlier_cursor"]
    ctx.script(ctx.answer("More."))
    ctx.chat("And then?", alice)   # two more messages since the page opened

    assert ids_of(earlier(api, alice, cursor)[1]) == [f"m{n}" for n in range(1, 11)]
    assert opened(api, alice)["earlier_cursor"] != cursor


def test_a_short_conversation_has_nothing_earlier(ctx, api, alice):
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)
    assert opened(api, alice)["earlier_cursor"] is None


def test_reading_back_takes_the_users_own_conversation_and_a_cursor_it_gave(ctx, api, alice):
    a_long_conversation(ctx, alice, 250)
    cursor = opened(api, alice)["earlier_cursor"]

    assert earlier(api, claims("bob", "t1", "member"), cursor)[0] == 404
    for bad in ("not-a-cursor", *(base64.urlsafe_b64encode(raw).decode() for raw in (b'["m1"]', b"[0]"))):
        status, body = earlier(api, alice, bad)
        assert status == 400 and "earlier_cursor" in body["message"], bad
    assert api.call("GET", f"/assistant/conversations/{THREAD}/messages", alice)[0] == 422   # no ?before=
    assert earlier(api, alice, cursor, limit=201)[0] == 422


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
    # Nor does a message bring it back, with its old messages, before TTL removes it.
    assert ctx.send("Still there?", alice)[0] == 404


def test_a_conversation_expires_whole_some_days_after_its_last_message(ctx, alice):
    ctx.history.days = ctx.service.days = 30   # a stack with AssistantConversationDays=30
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)
    first = stored(ctx).expires_at
    time.sleep(1.1)
    ctx.script(ctx.answer("Sure."))
    ctx.chat("And again", alice)

    # Only the conversation has a TTL, moved on by each message: a long
    # conversation never loses its oldest messages.
    assert stored(ctx).expires_at > first
    assert abs(stored(ctx).expires_at - (time.time() + 30 * 86400)) < 60
    items = [i for i in ctx.table.scan()["Items"] if i["sk"].startswith("MSG#")]
    assert len(items) == 4 and not [i for i in items if "expires_at" in i]


# ---------- keeping a conversation, and the agent's side of it ----------

def meta_item(ctx, user_id="alice", thread_id=THREAD) -> dict:
    return ctx.table.get_item(Key={"pk": f"TENANT#t1#USER#{user_id}#CONV#{thread_id}", "sk": "META"})["Item"]


def test_a_conversation_is_kept_until_its_user_deletes_it(ctx, api, alice):
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)

    assert "expires_at" not in meta_item(ctx)   # no TTL: DynamoDB never removes it
    assert [c["thread_id"] for c in api.call("GET", "/assistant/conversations", alice)[1]["items"]] == [THREAD]
    assert [m["content"] for m in opened(api, alice)["messages"]] == ["Hi", "Hello!"]


def test_a_conversation_that_was_to_expire_is_kept_from_its_next_message(ctx, alice):
    ctx.history.days = ctx.service.days = 30   # saved while the stack had them expire, as dev's were
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)
    assert stored(ctx).expires_at is not None

    ctx.history.days = ctx.service.days = 0
    ctx.script(ctx.answer("Sure."))
    ctx.chat("And again", alice)

    assert "expires_at" not in meta_item(ctx) and stored(ctx).message_count == 4


def expire_checkpoints(ctx, user: dict, agent: str = "assistant", thread_id: str = THREAD) -> None:
    """What CHECKPOINT_DAYS without a message does: the agent's checkpoints of the conversation are gone."""
    config = thread_config(AgentContext(user=user_from_claims(user), authorization="", session_id=thread_id), agent)
    ctx.state.checkpointer.delete_thread(config["configurable"]["thread_id"])
    assert ctx.state.checkpointer.get_tuple(config) is None


def sent(ctx, agent: str = "assistant") -> list[tuple[str, str]]:
    """What the agent's model got in its first call of the last run, but the system prompt."""
    return [(m.type, m.text) for m in ctx.model_calls(agent)[0] if m.type != "system"]


def test_an_agent_whose_checkpoints_expired_starts_again_from_the_history(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("You have no orders."))
    ctx.chat("What did we order?", alice)
    expire_checkpoints(ctx, alice)

    ctx.script(ctx.answer("Still none."))
    with captured_logs(assistant.app.logger) as logs:
        events = ctx.chat("And now?", alice)

    assert ctx.text(events) == "Still none."
    model_got = [m for m in ctx.model_calls()[0] if m.type != "system"]
    assert [m.type for m in model_got] == ["human", "ai", "tool", "ai", "human"]
    assert [(c["name"], c["args"]) for c in model_got[1].tool_calls] == [("list_orders", {"limit": 5})]
    assert model_got[2].tool_call_id == model_got[1].tool_calls[0]["id"]
    assert [m.text for m in model_got if m.type != "tool"] == ["What did we order?", "", "You have no orders.",
                                                               "And now?"]
    assert [line["messages"] for line in logs() if line["message"].startswith("Restored")] == [4]
    # It checkpoints again from there.
    config = thread_config(AgentContext(user=user_from_claims(alice), authorization="", session_id=THREAD),
                           "assistant")
    assert len(ctx.state.checkpointer.get_tuple(config).checkpoint["channel_values"]["messages"]) == 6


def test_each_agent_gets_back_only_its_own_side_of_the_conversation(ctx, alice):
    ctx.script(ctx.answer("Hello from the assistant."))
    ctx.chat("Hi assistant", alice)
    ctx.script(ctx.answer("Hello from the analyst."), agent="analyst")
    ctx.chat("Hi analyst", alice, agent="analyst")
    expire_checkpoints(ctx, alice)   # the assistant's; the analyst still has its own

    ctx.script(ctx.answer("Hello again."))
    ctx.chat("Hi again", alice)

    assert sent(ctx) == [("human", "Hi assistant"), ("ai", "Hello from the assistant."), ("human", "Hi again")]


def test_messages_saved_before_they_named_their_agent_are_the_main_agents(ctx, alice):
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)
    for item in ctx.table.scan()["Items"]:
        if item["sk"].startswith("MSG#"):
            ctx.table.update_item(Key={"pk": item["pk"], "sk": item["sk"]}, UpdateExpression="REMOVE #agent",
                                  ExpressionAttributeNames={"#agent": "agent"})   # a reserved word
    expire_checkpoints(ctx, alice)

    ctx.script(ctx.answer("Hello again!"))
    ctx.chat("Remember me?", alice)

    assert sent(ctx) == [("human", "Hi"), ("ai", "Hello!"), ("human", "Remember me?")]


def test_an_edit_after_the_checkpoints_expired_goes_back_as_the_history_did(ctx, alice):
    ctx.script(ctx.answer("Paris."))
    ctx.run(ctx.post("Capital of France?", alice, message_id="q1"), alice)
    ctx.script(ctx.answer("Berlin."))
    ctx.run(ctx.post("And Germany?", alice, message_id="q2"), alice)
    expire_checkpoints(ctx, alice)

    ctx.script(ctx.answer("Rome."))
    ctx.run(ctx.post("And Italy?", alice, message_id="q3", replaces="q2"), alice)

    assert sent(ctx) == [("human", "Capital of France?"), ("ai", "Paris."), ("human", "And Italy?")]


def runtime_role_allows(resource: str) -> set[str]:
    """The actions template.yaml's AssistantRuntimeRole allows on `resource` (e.g. "!GetAtt AssistantTable.Arn")."""
    class Template(yaml.SafeLoader):
        pass

    # CloudFormation's tags, as their text: "!GetAtt AssistantTable.Arn".
    Template.add_multi_constructor("!", lambda loader, tag, node:
                                   f"!{tag} {node.value}" if isinstance(node, yaml.ScalarNode) else None)
    template = yaml.load((Path(__file__).resolve().parents[3] / "template.yaml").read_text(), Template)
    role = template["Resources"]["AssistantRuntimeRole"]["Properties"]
    statements = [s for policy in role["Policies"] for s in policy["PolicyDocument"]["Statement"]]
    return {action for s in statements if resource in (s["Resource"] if isinstance(s["Resource"], list)
                                                       else [s["Resource"]]) for action in s["Action"]}


def test_the_runtime_role_allows_every_call_the_runtime_makes_to_the_assistant_table(ctx, alice):
    """
    moto checks no permissions, so a call the role doesn't allow fails only on
    AWS: every answer in a new conversation did, on dev, when restoring
    started reading the history with Query.
    """
    calls, recording = set(), threading.Event()

    def record(model, **_) -> None:   # returning anything else would answer the call instead of AWS
        if recording.is_set():
            calls.add(f"dynamodb:{model.name}")

    ctx.table.meta.client.meta.events.register("before-call.dynamodb", record)

    def runtime(run_id: str) -> None:
        """Only what the runtime does: not the HTTP API's own calls (post)."""
        recording.set()
        try:
            ctx.run(run_id, alice)
        finally:
            recording.clear()

    ctx.script(ctx.answer("Hello!"))
    runtime(ctx.post("Hi", alice))                  # a new conversation, titled meanwhile
    ctx.script(ctx.answer("Hi again."))
    runtime(ctx.post("Still there?", alice))
    expire_checkpoints(ctx, alice)
    ctx.script(ctx.answer("Yes."))
    runtime(ctx.post("And now?", alice))            # rebuilt from the history

    assert {"dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem"} <= calls
    assert calls <= runtime_role_allows("!GetAtt AssistantTable.Arn")


def test_a_tool_call_cut_off_mid_stream_is_left_out_of_what_the_agent_gets_back():
    history = [
        {"id": "u1", "role": "user", "content": "Count them"},
        {"id": "a1", "role": "assistant", "content": "Let me look.", "toolCalls": [
            {"id": "c1", "type": "function", "function": {"name": "list_orders", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": '{"command": "wc'}}]},
        {"id": "t1", "role": "tool", "toolCallId": "c1", "content": "[]"},
        {"id": "t2", "role": "tool", "toolCallId": "c2", "content": "Interrupted"},
    ]

    messages = agent_messages(history)

    assert [m.type for m in messages] == ["human", "ai", "tool"]
    assert messages[1].text == "Let me look." and [c["id"] for c in messages[1].tool_calls] == ["c1"]
    assert messages[2].tool_call_id == "c1" and messages[0].id == "u1"


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


TTL_SERVICE = {"type": "Service", "principalId": "dynamodb.amazonaws.com"}


def stream(*records: tuple[dict, dict | None]) -> dict:
    """
    A batch from the assistant table's stream: each (item, userIdentity), its
    removal as Lambda gets it (OLD_IMAGE). `userIdentity` is TTL's, or None
    for a delete by the app.
    """
    serialize = TypeSerializer().serialize
    batch = []
    for n, (item, identity) in enumerate(records, start=1):
        image = {key: serialize(value) for key, value in item.items()}
        record = {"eventID": f"e{n}", "eventName": "REMOVE", "eventVersion": "1.1", "eventSource": "aws:dynamodb",
                  "awsRegion": "ap-southeast-1",
                  "eventSourceARN": "arn:aws:dynamodb:ap-southeast-1:1:table/assistant-test/stream/2026",
                  "dynamodb": {"Keys": {"pk": image["pk"], "sk": image["sk"]}, "OldImage": image,
                               "SequenceNumber": str(n), "SizeBytes": 100, "StreamViewType": "OLD_IMAGE"}}
        if identity:
            record["userIdentity"] = identity
        batch.append(record)
    return {"Records": batch}


def test_the_purge_deletes_an_expired_conversation_once_ttl_removes_it(ctx, checkpoints, alice, monkeypatch):
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hi", alice)
    ctx.script(ctx.answer("Hello Bob!"))
    ctx.chat("Hi", claims("bob", "t1", "member"))   # another user's conversation, same thread id
    mine = checkpoint_session("t1:alice", THREAD, "assistant")
    checkpoints.converse(mine, "Hi")
    meta = ctx.table.get_item(Key={"pk": f"TENANT#t1#USER#alice#CONV#{THREAD}", "sk": "META"})["Item"]
    message = ctx.table.get_item(Key={"pk": meta["pk"], "sk": "MSG#000001"})["Item"]
    ctx.table.delete_item(Key={"pk": meta["pk"], "sk": "META"})   # what TTL did
    monkeypatch.setattr(assistant.purge, "get_checkpoint_purge", lambda: checkpoints.purge)
    monkeypatch.setattr(assistant.purge, "get_purged_conversations", lambda: ctx.conversations)

    # Only TTL's removal of a META counts: not the app's delete, nor an old message's own TTL.
    ignored = stream((meta, None), (message, TTL_SERVICE))
    assert assistant.purge.handler(ignored, FakeContext()) == {"batchItemFailures": []}
    assert checkpoints.left(mine) != {} and [i for i in ctx.table.scan()["Items"] if i["pk"] == meta["pk"]]

    assert assistant.purge.handler(stream((meta, TTL_SERVICE)), FakeContext()) == {"batchItemFailures": []}

    assert checkpoints.left(mine) == {}
    assert not [i for i in ctx.table.scan()["Items"] if i["pk"] == meta["pk"]]
    assert [m["content"] for m in ctx.conversations.messages("t1", "bob", THREAD, 50)] == ["Hi", "Hello Bob!"]


def member_removed(tenant_id: str, user_id: str, occurred_at: str) -> dict:
    """accounts' MemberRemoved as EventBridge puts it on the purge queue."""
    return {"version": "0", "id": str(uuid4()), "detail-type": "MemberRemoved", "source": "saas.app",
            "account": "123456789012", "time": "2026-10-24T10:00:00Z", "region": "ap-southeast-1", "resources": [],
            "detail": {"tenant_id": tenant_id, "event_id": str(uuid4()), "occurred_at": occurred_at,
                       "user_id": user_id, "removed_by": "owner"}}


def test_a_removed_members_conversations_memories_and_files_go(ctx, checkpoints):
    alice, bob = claims("alice", "t1", "member"), claims("bob", "t1", "member")
    for user in (alice, bob):
        ctx.memories.remember(user_from_claims(user), "Likes short answers")
        ctx.files.save(user_from_claims(user), name="report.md", data=b"# Hi", content_type=None, thread_id=THREAD,
                       agent="assistant")
    ctx.memories.set_enabled(user_from_claims(alice), False)
    start(ctx, thread(1), "2026-10-24T08:00:00.000Z")
    start(ctx, thread(2), "2026-10-24T09:00:00.000Z", expires_in=-60)   # expired, TTL hasn't removed it yet
    start(ctx, thread(3), "2026-10-24T08:00:00.000Z", user_id="bob")    # someone else's
    start(ctx, thread(4), "2026-10-24T11:00:00.000Z")   # after the removal: invited back, the event came late
    mine, bobs = checkpoint_session("t1:alice", thread(1), "assistant"), checkpoint_session("t1:bob", thread(3),
                                                                                           "assistant")
    checkpoints.converse(mine, "Hi")
    checkpoints.converse(bobs, "Hi")
    message = sqs(member_removed("t1", "alice", "2026-10-24T10:00:00.000000+00:00"))

    with captured_logs(assistant.purge.logger) as logs:
        assert process_batch(message, FakeContext(), checkpoints.purge, ctx.conversations,
                             ctx.memories.memories, ctx.files) == {"batchItemFailures": []}

    assert {i["pk"] for i in ctx.table.scan()["Items"] if "#CONV#" in i["pk"]} == {
        f"TENANT#t1#USER#alice#CONV#{thread(4)}", f"TENANT#t1#USER#bob#CONV#{thread(3)}"}
    assert ctx.memories.read(user_from_claims(alice)) == (True, [])   # memories and setting
    assert len(ctx.memories.read(user_from_claims(bob))[1]) == 1
    assert ctx.files.list(user_from_claims(alice), 10)[0] == []
    assert len(ctx.files.list(user_from_claims(bob), 10)[0]) == 1
    objects = ctx.s3.list_objects_v2(Bucket="assistant-files-test")["Contents"]
    assert [o["Key"].split("/")[1] for o in objects] == ["bob"]
    assert checkpoints.left(mine) == {} and checkpoints.left(bobs) != {}
    assert [line["conversations"] for line in logs() if line["message"].startswith("Purged a removed")] == [2]
    # Delivered again (at least once): nothing left to do, nothing else goes.
    assert process_batch(message, FakeContext(), checkpoints.purge, ctx.conversations) == {"batchItemFailures": []}
    assert len([i for i in ctx.table.scan()["Items"] if "#CONV#" in i["pk"]]) == 4


# ---------- the Lambdas stay light ----------

def test_the_lambdas_never_import_the_agents_dependencies():
    # They get the shared layer, not LangChain, the AgentCore SDK, AG-UI or httpx (the Runtime's zip has those).
    heavy = ("langchain", "langchain_core", "langgraph", "langchain_aws", "langchain_openai", "ag_ui",
             "bedrock_agentcore", "httpx", "yaml", "openai")
    script = ("import sys, assistant.handler, assistant.purge; "
              f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({heavy!r})))")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
