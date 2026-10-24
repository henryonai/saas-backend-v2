"""
Conversations: what the agent keeps as it answers (app.py, ConversationLog,
TitleMiddleware), the HTTP API that lists, reopens, renames and deletes them
(handler.py), and the worker that deletes them from AgentCore Memory (purge.py).
"""
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ag_ui.core import Message
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError
from botocore.exceptions import ClientError
from pydantic import TypeAdapter

from assistant.container import get_conversation_service
from assistant.context import checkpoint_session
from assistant.handler import app, handler
from assistant.memory import AgentCoreMemory
from assistant.purge import process_batch
from assistant.service import ConversationService
from testkit.http import FakeContext, call, claims

THREAD = "11111111-2222-3333-4444-555555555555"   # conftest's SESSION: the threadId is the session
AGUI_MESSAGE = TypeAdapter(Message)


def user_message(text: str) -> dict:
    return {"id": str(uuid4()), "role": "user", "content": text}


def stored(ctx, user_id="alice", tenant_id="t1", thread_id=THREAD):
    return ctx.conversations.get(tenant_id, user_id, thread_id)


@pytest.fixture
def api(ctx):
    purges: list[tuple[str, list[str]]] = []
    service = ConversationService(ctx.conversations,
                                  purge=SimpleNamespace(send=lambda actor, sessions: purges.append((actor, sessions))))
    app.dependency_overrides[get_conversation_service] = lambda: service
    yield SimpleNamespace(call=lambda *a, **kw: call(handler, *a, **kw), purges=purges)
    app.dependency_overrides.clear()


# ---------- what the agent keeps ----------

def test_the_history_is_what_the_page_showed_as_agui_messages(ctx, alice):
    ctx.api.responses["/orders"] = {"items": [{"order_id": "o1", "item": "Book"}], "next_cursor": None}
    ctx.script(ctx.tool_call("list_orders", {"limit": 5}), ctx.answer("You have one order: a Book."))
    question = user_message("What did we order?")

    events = ctx.chat(None, alice, messages=[question])

    messages = ctx.conversations.messages("t1", "alice", THREAD, 50)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[0] == question                       # the client's own id: both sides agree
    [call] = messages[1]["toolCalls"]
    assert call["function"] == {"name": "list_orders", "arguments": json.dumps({"limit": 5})}
    assert messages[2]["toolCallId"] == call["id"] and "Book" in messages[2]["content"]
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


def test_a_message_the_plan_refuses_keeps_nothing(ctx, alice):
    ctx.set_usage("t1", messages=50, tokens=0)
    assert ctx.error(ctx.chat("Hi", alice))["code"] == "PLAN_LIMIT_REACHED"
    assert stored(ctx) is None


def test_an_answer_that_ends_after_a_delete_does_not_bring_the_conversation_back(ctx):
    ctx.conversations.start("t1", "alice", THREAD, agent="assistant", title="Hi", message=user_message("Hi"),
                            now="2026-10-24T08:00:00.000Z", expires_at=int(time.time()) + 3600)
    ctx.conversations.delete("t1", "alice", THREAD)

    assert ctx.conversations.add_messages("t1", "alice", THREAD, [{"id": "a1", "role": "assistant", "content": "x"}],
                                          now="2026-10-24T08:00:01.000Z", expires_at=int(time.time()) + 3600) is False
    assert ctx.table.scan()["Items"] == []


# ---------- the HTTP API ----------

def start(ctx, thread_id: str, now: str, user_id="alice", tenant_id="t1", expires_in=3600, title="Hi"):
    ctx.conversations.start(tenant_id, user_id, thread_id, agent="assistant", title=title,
                            message=user_message(title), now=now, expires_at=int(time.time()) + expires_in)


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
    start(ctx, thread(1), "2026-10-24T08:01:00.000Z", title="Older")
    start(ctx, thread(2), "2026-10-24T08:02:00.000Z", title="Newer")
    ctx.conversations.add_messages("t1", "alice", thread(1), [{"id": "a", "role": "assistant", "content": "x"}],
                                   now="2026-10-24T08:03:00.000Z", expires_at=int(time.time()) + 3600)

    assert [c["title"] for c in api.call("GET", "/assistant/conversations", alice)[1]["items"]] == ["Older", "Newer"]


def test_opening_a_conversation_returns_its_messages(ctx, api, alice):
    ctx.script(ctx.answer("Hello Alice!"))
    ctx.chat("Hi", alice)

    status, conversation = api.call("GET", f"/assistant/conversations/{THREAD}", alice)

    assert status == 200 and conversation["title"] == "Test conversation" and conversation["message_count"] == 2
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [("user", "Hi"),
                                                                             ("assistant", "Hello Alice!")]


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
    # The exchange's session and the agent's checkpoints, in the same actor's memory.
    assert api.purges == [("t1:alice", [THREAD, checkpoint_session("t1:alice", THREAD, "assistant")])]


def test_nobody_reaches_another_users_conversation(ctx, api):
    start(ctx, THREAD, "2026-10-24T08:00:00.000Z")
    for intruder in (claims("bob", "t1", "member"), claims("alice", "t2", "owner")):
        assert api.call("GET", "/assistant/conversations", intruder)[1]["items"] == []
        assert api.call("GET", f"/assistant/conversations/{THREAD}", intruder)[0] == 404
        assert api.call("PATCH", f"/assistant/conversations/{THREAD}", intruder, body={"title": "x"})[0] == 404
        assert api.call("DELETE", f"/assistant/conversations/{THREAD}", intruder)[0] == 404
    assert stored(ctx).title == "Hi" and api.purges == []


def test_expired_conversations_are_gone_before_ttl_removes_them(ctx, api, alice):
    start(ctx, THREAD, "2026-10-24T08:00:00.000Z", expires_in=-60)
    assert api.call("GET", "/assistant/conversations", alice)[1]["items"] == []
    assert api.call("GET", f"/assistant/conversations/{THREAD}", alice)[0] == 404


def test_bad_input(api, alice):
    assert api.call("GET", "/assistant/conversations", alice, query={"cursor": "not-a-cursor"})[0] == 400
    assert api.call("GET", "/assistant/conversations/short", alice)[0] == 404
    assert api.call("GET", "/assistant/conversations", claims("alice"))[0] == 403   # no workspace yet


# ---------- the purge worker ----------

class FakeMemoryClient:
    """AgentCore Memory's data plane, for list_events and delete_event: 100 events a page."""

    def __init__(self, events: dict[tuple[str, str], list[str]]):
        self.events = events
        self.fail_on: str | None = None

    def list_events(self, memoryId, actorId, sessionId, includePayloads, maxResults, nextToken=None):
        ids = self.events.get((actorId, sessionId), [])
        start = int(nextToken or 0)
        page = {"events": [{"eventId": i} for i in ids[start:start + maxResults]]}
        if start + maxResults < len(ids):
            page["nextToken"] = str(start + maxResults)
        return page

    def delete_event(self, memoryId, actorId, sessionId, eventId):
        if eventId == self.fail_on:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteEvent")
        self.events[(actorId, sessionId)].remove(eventId)


def sqs(*bodies: dict) -> dict:
    return {"Records": [{"messageId": f"m{n}", "receiptHandle": f"r{n}", "body": json.dumps(body),
                         "attributes": {}, "messageAttributes": {}, "md5OfBody": "", "eventSource": "aws:sqs",
                         "awsRegion": "ap-southeast-1", "eventSourceARN": "arn:aws:sqs:ap-southeast-1:1:q"}
                        for n, body in enumerate(bodies, start=1)]}


def test_the_purge_deletes_every_event_of_the_conversation_and_nothing_else():
    client = FakeMemoryClient({("t1:alice", THREAD): ["e1", "e2"],
                               ("t1:alice", "checkpoints"): [f"c{n}" for n in range(250)],   # several pages
                               ("t1:alice", "another"): ["keep"]})
    memory = AgentCoreMemory(client, "memory-1")

    result = process_batch(sqs({"actor_id": "t1:alice", "session_ids": [THREAD, "checkpoints"]}), FakeContext(),
                           memory)

    assert result == {"batchItemFailures": []}
    assert client.events == {("t1:alice", THREAD): [], ("t1:alice", "checkpoints"): [],
                             ("t1:alice", "another"): ["keep"]}


def test_a_failed_purge_is_retried_and_finishes_the_job():
    client = FakeMemoryClient({("t1:alice", THREAD): ["e1", "e2", "e3"]})
    client.fail_on = "e2"
    memory = AgentCoreMemory(client, "memory-1")
    message = sqs({"actor_id": "t1:alice", "session_ids": [THREAD]})

    with pytest.raises(BatchProcessingError):   # the whole batch (its one message) failed: SQS retries it
        process_batch(message, FakeContext(), memory)
    client.fail_on = None
    assert process_batch(message, FakeContext(), memory) == {"batchItemFailures": []}
    assert client.events[("t1:alice", THREAD)] == []


# ---------- the Lambdas stay light ----------

def test_the_lambdas_never_import_the_agents_dependencies():
    # They get the shared layer, not LangChain, the AgentCore SDK, AG-UI or httpx (the Runtime's zip has those).
    heavy = ("langchain", "langchain_core", "langgraph", "langchain_aws", "langchain_openai", "ag_ui",
             "bedrock_agentcore", "httpx", "yaml", "openai")
    script = ("import sys, assistant.handler, assistant.purge; "
              f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({heavy!r})))")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
