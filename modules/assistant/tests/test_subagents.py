"""Delegation: the assistant hands a task to the analyst (its `subagents:`), which starts fresh and reports back."""
from langchain_core.messages import AIMessage

from assistant.agent_names import agent_names
from assistant.middleware import ALLOWANCE_USED
from assistant.models import current_month
from testkit.http import claims

PRO = claims("alice", "t1", "member", plan="pro")


def delegate(instructions: str, call_id: str = "task-1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": "task", "id": call_id,
                                      "args": {"agent": "analyst", "instructions": instructions}}],
                     usage_metadata={"input_tokens": 200, "output_tokens": 30, "total_tokens": 230})


def test_the_assistant_delegates_and_explains_the_result(ctx):
    ctx.script(delegate("Total value of paid orders this month?"),
               ctx.answer("Your paid orders this month total $310."))
    ctx.script(ctx.answer("Paid orders total 31000 cents (3 orders).", input_tokens=500, output_tokens=40),
               agent="analyst")

    events = ctx.chat("How much did we spend this month?", PRO)

    assert "task" in ctx.tools(events)
    assert ctx.text(events) == "Your paid orders this month total $310."   # only the assistant speaks to the user
    # The analyst started fresh: its own prompt and the instructions, not the conversation.
    system, task = ctx.model_calls("analyst")[0]
    assert system.content.startswith("You are a data analyst")
    assert task.content == "Total value of paid orders this month?"
    # Its answer came back to the assistant as the tool's result.
    assert ctx.model_calls()[1][-1].content == "Paid orders total 31000 cents (3 orders)."


def test_a_subagents_tokens_count_against_the_same_allowance(ctx):
    ctx.script(delegate("Count orders."), ctx.answer("Done.", input_tokens=100, output_tokens=10))
    ctx.script(ctx.answer("12 orders.", input_tokens=500, output_tokens=40), agent="analyst")

    ctx.chat("How many orders?", PRO)

    usage = ctx.usage.usage.get("t1", current_month())
    assert usage.tokens == 230 + 540 + 110 and usage.messages == 1   # one message, three model calls


def test_the_subagent_works_as_the_same_user_in_the_same_sandbox(ctx):
    ctx.api.responses["/orders"] = {"items": [], "next_cursor": None}
    ctx.script(ctx.tool_call("bash", {"command": "echo start > notes.txt"}), delegate("Analyze."),
               ctx.answer("Done."))
    ctx.script(ctx.tool_call("list_orders", {"limit": 50}), ctx.tool_call("bash", {"command": "cat notes.txt"}),
               ctx.answer("No orders yet."), agent="analyst")

    ctx.chat("Analyze our orders", PRO)

    assert ctx.api.calls[0]["authorization"].startswith("Bearer ")              # the user's token
    assistant_run, analyst_run = ctx.sandbox.commands
    assert assistant_run["conversation_id"] == analyst_run["conversation_id"]  # same sandbox, same files


def test_only_listed_subagents_and_no_further_delegation(ctx):
    ctx.script(delegate("Anything."), ctx.answer("Done."))
    ctx.script(ctx.answer("Result."), agent="analyst")
    ctx.chat("Go", PRO)

    assert "task" in ctx.tools_shown()[0]
    assert "task" not in ctx.tools_shown("analyst")[0]       # subagents can't delegate (one level)


def test_an_unknown_subagent_is_an_error_result(ctx):
    bad = AIMessage("", tool_calls=[{"name": "task", "id": "t", "args": {"agent": "nobody", "instructions": "x"}}])
    ctx.script(bad, ctx.answer("Sorry."))
    ctx.chat("Go", PRO)
    assert "there is no agent 'nobody'" in ctx.model_calls()[1][-1].content


def test_no_subagent_starts_once_the_allowance_is_used(ctx):
    ctx.set_usage("t1", messages=0, tokens=19_999_900)       # Pro: 20M; the assistant's first call reaches it
    ctx.script(delegate("Analyze."), ctx.answer("never sent"))

    events = ctx.chat("Analyze", PRO)

    assert ctx.model_calls("analyst") == []                  # the task tool refused to start it
    assert ctx.text(events) == ALLOWANCE_USED                # and the assistant stopped cleanly


def test_a_subagent_keeps_no_checkpoints(ctx):
    ctx.script(delegate("Analyze."), ctx.answer("Done."))
    ctx.script(ctx.answer("Result."), agent="analyst")
    ctx.chat("Analyze", PRO)
    threads = {key[0] for key in ctx.state.checkpointer.storage}
    assert len(threads) == 1                                  # the assistant's conversation only


def test_the_frontend_can_talk_to_any_agent_directly(ctx):
    ctx.script(ctx.answer("Hi, I'm the analyst."), agent="analyst")
    events = ctx.chat("Hello", PRO, agent="analyst")
    assert ctx.text(events) == "Hi, I'm the analyst." and events[-1]["result"] == {"agent": "analyst"}

    # Each agent has its own side of the conversation: the assistant didn't see the analyst's.
    ctx.script(ctx.answer("Hello!"))
    ctx.chat("Hello again", PRO)
    assert [m.content for m in ctx.model_calls()[0] if m.type == "human"] == ["Hello again"]


def test_an_unknown_agent_is_refused_before_anything_counts(ctx):
    status, body = ctx.send("Hello", PRO, agent="nobody")
    assert status == 404 and "assistant" in body["message"]
    assert ctx.usage.usage.get("t1", current_month()).messages == 0


def test_the_http_api_knows_the_agents_without_loading_them(ctx):
    # agent_names() reads the names the way the Lambda can (no YAML); load_agents() is the truth.
    assert agent_names() == set(ctx.definitions)
