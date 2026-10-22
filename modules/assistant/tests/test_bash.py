"""The bash tool: a terminal in the conversation's sandbox, for plans that include it."""
from assistant.models import current_month
from assistant.sandbox import CommandResult
from assistant.tools import MAX_OUTPUT_CHARS
from testkit.http import claims

PRO = claims("alice", "t1", "member", plan="pro")


def sessions_used(ctx, tenant_id="t1"):
    return ctx.usage.usage.get(tenant_id, current_month()).sandbox_sessions


def test_commands_run_in_the_conversations_sandbox(ctx):
    ctx.sandbox.result = CommandResult(exit_code=0, stdout="42\n", stderr="")
    ctx.script(ctx.tool_call("bash", {"command": "python -c 'print(6*7)'"}), ctx.answer("It's 42."))

    events = ctx.chat("What is 6 times 7? Compute it.", PRO)

    assert {"type": "tool", "name": "bash"} in events and events[-1]["type"] == "done"
    [run] = ctx.sandbox.commands
    assert run["command"] == "python -c 'print(6*7)'"
    tool_result = ctx.model_calls()[1][-1]
    assert tool_result.type == "tool" and "exit code: 0" in tool_result.content and "42" in tool_result.content
    assert sessions_used(ctx) == 1


def test_the_sandbox_is_kept_for_the_conversation_and_counted_once(ctx):
    ctx.script(ctx.tool_call("bash", {"command": "echo hi > notes.txt"}), ctx.answer("Saved."))
    ctx.chat("Save a note", PRO)
    ctx.script(ctx.tool_call("bash", {"command": "cat notes.txt"}), ctx.answer("It says hi."))
    ctx.chat("Read it back", PRO)

    first, second = ctx.sandbox.commands
    assert first["conversation_id"] == second["conversation_id"]
    assert sessions_used(ctx) == 1


def test_each_user_gets_their_own_sandbox_even_with_the_same_session_id(ctx):
    for user in (PRO, claims("bob", "t1", "member", plan="pro")):
        ctx.script(ctx.tool_call("bash", {"command": "ls"}), ctx.answer("Done."))
        ctx.chat("List files", user)
    alice_run, bob_run = ctx.sandbox.commands
    assert alice_run["conversation_id"] != bob_run["conversation_id"]


def test_free_plan_does_not_see_the_tool_and_cannot_use_it(ctx, alice):
    ctx.script(ctx.answer("I can't run code on your plan."))
    ctx.chat("Run ls", alice)
    assert "bash" not in ctx.tools_shown()[0] and "list_orders" in ctx.tools_shown()[0]

    # A model can still call a tool it wasn't shown: the tool checks the plan itself.
    ctx.script(ctx.tool_call("bash", {"command": "ls"}), ctx.answer("Not on your plan."))
    ctx.chat("Run ls anyway", alice)
    assert ctx.sandbox.commands == [] and sessions_used(ctx) == 0
    assert "not included in the free plan" in ctx.model_calls()[1][-1].content


def test_monthly_sandbox_sessions_are_capped(ctx):
    ctx.set_usage("t1", messages=0, tokens=0, sandbox_sessions=200)   # Pro: 200 a month
    ctx.script(ctx.tool_call("bash", {"command": "ls"}), ctx.answer("Limit reached."))

    ctx.chat("Run ls", PRO)

    assert ctx.sandbox.commands == []
    assert "200 assistant terminal sessions a month" in ctx.model_calls()[1][-1].content


def test_long_output_keeps_its_start_and_end(ctx):
    ctx.sandbox.result = CommandResult(exit_code=1, stdout="start " + "x" * 50_000 + " end",
                                       stderr="Traceback: boom")
    ctx.script(ctx.tool_call("bash", {"command": "python big.py"}), ctx.answer("It failed."))

    ctx.chat("Run big.py", PRO)

    result = ctx.model_calls()[1][-1].content
    assert result.startswith("exit code: 1") and "start" in result and "end" in result
    assert "characters cut" in result and "Traceback: boom" in result
    assert len(result) < 2 * MAX_OUTPUT_CHARS + 500


def test_a_restarted_sandbox_is_announced(ctx):
    ctx.sandbox.result = CommandResult(exit_code=1, stdout="", stderr="cat: notes.txt: No such file",
                                       restarted=True)
    ctx.script(ctx.tool_call("bash", {"command": "cat notes.txt"}), ctx.answer("The file is gone."))
    ctx.chat("Read my note", PRO)
    assert "new, empty one" in ctx.model_calls()[1][-1].content
