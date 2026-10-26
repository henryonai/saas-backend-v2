"""
The tools agents can use. An agent's Markdown file names the ones it gets
(`tools:` in its frontmatter); this is the list of names it can choose from.

orders.py  list_orders, get_order: the orders module's HTTP API, called with
           the user's own token (the API checks tenant and role, and no module
           imports another). The token reaches tools through runtime.context,
           never through the model.
usage.py   assistant_usage: this month's usage and the plan's limits.
bash.py    bash: a shell in the conversation's sandbox.
memory.py  remember, forget: what the assistant remembers about the user,
           for agents with `memory: user` only.

Tools return errors as data so the model can explain them instead of the whole
message failing. No tool changes the app's data: one that does needs an
idempotency key and, usually, the user's approval (HumanInTheLoopMiddleware),
because a retried or resumed run can call it twice. To add a tool, write it in
one of these files (or a new one) and add it to make_tools.
"""
from langchain_core.tools import BaseTool

from assistant.sandbox import Sandbox
from assistant.service import MemoryService, UsageService
from assistant.tools.bash import MAX_OUTPUT_CHARS, make_bash_tool
from assistant.tools.memory import make_memory_tools
from assistant.tools.orders import AppApi, HttpAppApi, make_order_tools
from assistant.tools.usage import make_usage_tool

SANDBOX_TOOLS = {"bash"}   # need a plan with sandbox sessions (PlanToolsMiddleware hides them otherwise)


def make_tools(api: AppApi, usage: UsageService, sandbox: Sandbox, memories: MemoryService) -> dict[str, BaseTool]:
    """Every tool, by name."""
    tools = [*make_order_tools(api), make_usage_tool(usage), make_bash_tool(sandbox, usage),
             *make_memory_tools(memories)]
    return {t.name: t for t in tools}


__all__ = ["MAX_OUTPUT_CHARS", "SANDBOX_TOOLS", "AppApi", "HttpAppApi", "make_tools"]
