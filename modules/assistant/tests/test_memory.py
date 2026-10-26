"""
What the assistant remembers about each user across conversations (saved
memories, as ChatGPT's): an agent with `memory: user` is sent them and saves,
corrects and forgets them with its tools (MemoryMiddleware, tools/memory.py);
the user sees, adds, edits and deletes them, or turns memory off (the HTTP
API, /assistant/memories).
"""
import pytest
from langchain_core.messages import SystemMessage

from assistant.definitions import AgentDefinitionError, parse_agent
from assistant.service import MAX_MEMORIES
from shared.auth import user_from_claims
from testkit.http import call, claims
from testkit.logs import captured_logs

import assistant.service
from assistant.handler import handler

TOOLS = {"list_orders", "bash", "remember", "forget"}


def memories(user) -> dict:
    status, body = call(handler, "GET", "/assistant/memories", user)
    assert status == 200, body
    return body


def texts(user) -> list[str]:
    return [m["text"] for m in memories(user)["items"]]


def system_prompt(ctx, call: int = 0) -> str:
    """What the main agent's model was told on a model call: its system prompt, memories and all."""
    return next(m.text for m in ctx.model_calls()[call] if isinstance(m, SystemMessage))


def test_the_assistant_is_sent_what_it_remembers_about_the_user(ctx, alice):
    status, memory = call(handler, "POST", "/assistant/memories", alice, body={"text": "  Prefers  metric units "})
    assert status == 201 and memory["text"] == "Prefers metric units" and memory["thread_id"] is None
    ctx.script(ctx.answer("Noted."))

    ctx.chat("How far is Hanoi from Hue?", alice)

    prompt = system_prompt(ctx)
    assert prompt.startswith("You are the assistant inside a business app.")   # its own prompt, then what it remembers
    assert f"- [{memory['memory_id']}] Prefers metric units" in prompt
    assert {"remember", "forget"} <= set(ctx.tools_shown()[0])


def test_the_assistant_saves_corrects_and_forgets_what_the_user_tells_it(ctx, alice):
    ctx.script(ctx.tool_call("remember", {"fact": "Works at the Hanoi office"}), ctx.answer("I'll remember that."))
    with captured_logs(assistant.service.logger) as logs:
        ctx.chat("I work at our Hanoi office.", alice)

    [memory] = memories(alice)["items"]
    assert memory["text"] == "Works at the Hanoi office" and memory["thread_id"] == ctx.session
    assert f"[{memory['memory_id']}] Works at the Hanoi office" in system_prompt(ctx, 1)   # the next call knows it
    assert "Hanoi" not in str(logs())   # never its text in the logs

    ctx.script(ctx.tool_call("remember", {"fact": "Works at the Da Nang office", "replaces": memory["memory_id"]}),
               ctx.answer("Updated."))
    ctx.chat("I moved to Da Nang.", alice)
    assert texts(alice) == ["Works at the Da Nang office"]

    ctx.script(ctx.tool_call("forget", {"memory_id": memory["memory_id"]}), ctx.answer("Forgotten."))
    ctx.chat("Forget where I work.", alice)
    assert texts(alice) == [] and "(Nothing yet.)" in system_prompt(ctx, 1)


def test_with_memory_off_the_assistant_neither_sees_nor_saves_memories(ctx, alice):
    call(handler, "POST", "/assistant/memories", alice, body={"text": "Prefers metric units"})
    status, body = call(handler, "PATCH", "/assistant/memories", alice, body={"enabled": False})
    assert status == 200 and body["enabled"] is False and len(body["items"]) == 1   # what is saved stays
    ctx.script(ctx.tool_call("remember", {"fact": "Likes tea"}), ctx.answer("OK."))   # it calls it anyway

    events = ctx.chat("I like tea.", alice)

    assert "metric" not in system_prompt(ctx) and "What you remember" not in system_prompt(ctx)
    assert not {"remember", "forget"} & set(ctx.tools_shown()[0])
    result = next(e["content"] for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result.startswith("Error: Memory is off") and texts(alice) == ["Prefers metric units"]
    call(handler, "PATCH", "/assistant/memories", alice, body={"enabled": True})
    assert memories(alice)["enabled"] is True


def test_only_agents_with_memory_have_it(ctx):
    assert ctx.definitions["assistant"].memory and not ctx.definitions["analyst"].memory
    assert not {"remember", "forget"} & set(ctx.definitions["analyst"].tools)


def test_memory_in_an_agents_markdown():
    def parse(front: str):
        return parse_agent(f"---\nname: helper\ndescription: Helps.\n{front}\n---\nYou help.\n", "helper.md", TOOLS)

    assert parse("memory: user").tools == ("bash", "forget", "list_orders", "remember")
    assert parse("").tools == ("bash", "list_orders")   # every tool, but not memory's
    assert parse("tools: bash\nmemory: user").tools == ("bash", "forget", "remember")
    with pytest.raises(AgentDefinitionError, match="only be user"):
        parse("memory: project")
    with pytest.raises(AgentDefinitionError, match="come with `memory: user`"):
        parse("tools: bash, remember")


def test_the_user_edits_and_deletes_memories(ctx, alice):
    ids = [call(handler, "POST", "/assistant/memories", alice, body={"text": text})[1]["memory_id"]
           for text in ("Likes tea", "Works in Hanoi")]
    assert call(handler, "POST", "/assistant/memories", alice, body={"text": "likes TEA"})[1]["memory_id"] == ids[0]

    status, edited = call(handler, "PATCH", f"/assistant/memories/{ids[1]}", alice, body={"text": "Works in Hue"})
    assert status == 200 and edited["text"] == "Works in Hue"
    assert call(handler, "DELETE", f"/assistant/memories/{ids[0]}", alice)[0] == 204
    assert texts(alice) == ["Works in Hue"]
    assert call(handler, "DELETE", f"/assistant/memories/{ids[0]}", alice)[0] == 404
    assert call(handler, "PATCH", "/assistant/memories/nope", alice, body={"text": "x"})[0] == 404
    call(handler, "PATCH", "/assistant/memories", alice, body={"enabled": False})
    assert call(handler, "DELETE", "/assistant/memories", alice)[0] == 204
    assert memories(alice) == {"enabled": False, "items": [], "limit": MAX_MEMORIES}   # off stays off


def test_each_user_has_their_own_memories(ctx, alice):
    bob = claims("bob", "t1", "member")
    memory = call(handler, "POST", "/assistant/memories", alice, body={"text": "Likes tea"})[1]

    assert texts(bob) == [] and texts(claims("alice", "t2", "member")) == []   # another workspace too
    assert call(handler, "DELETE", f"/assistant/memories/{memory['memory_id']}", bob)[0] == 404
    assert call(handler, "PATCH", f"/assistant/memories/{memory['memory_id']}", bob, body={"text": "x"})[0] == 404
    assert texts(alice) == ["Likes tea"]


def test_memories_are_short_and_few(ctx, alice):
    assert call(handler, "POST", "/assistant/memories", alice, body={"text": "x" * 501})[0] == 422
    assert call(handler, "POST", "/assistant/memories", alice, body={"text": "   "})[0] == 400
    user = user_from_claims(alice)
    for n in range(MAX_MEMORIES):
        ctx.memories.remember(user, f"Fact {n}")

    status, body = call(handler, "POST", "/assistant/memories", alice, body={"text": "One more"})

    assert status == 409 and "delete some first" in body["message"]
    ctx.script(ctx.tool_call("remember", {"fact": "One more"}), ctx.answer("I can't."))
    result = next(e["content"] for e in ctx.chat("Remember one more", alice) if e["type"] == "TOOL_CALL_RESULT")
    assert "delete some first" in result
