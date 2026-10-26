"""
Middleware: what runs around an agent's model and tool calls. factory.py puts
them in order for each agent.

usage.py         UsageMiddleware: counts every model call's tokens against the
                 workspace's monthly cap, and stops the agent cleanly at it.
conversation.py  ConversationMiddleware: keeps the conversation valid after a
                 message was cut off, and sends the model only recent history.
plan_tools.py    PlanToolsMiddleware: shows the model only the tools the
                 caller's plan includes.
errors.py        tool_failed: a tool's exception becomes a result the model can
                 explain (for the built-in ToolErrorMiddleware).

A conversation's title is written beside the run, not in it (titles.py).
"""
from assistant.middleware.conversation import INTERRUPTED, ConversationMiddleware, recent_history
from assistant.middleware.errors import tool_failed
from assistant.middleware.plan_tools import PlanToolsMiddleware
from assistant.middleware.usage import ALLOWANCE_USED, UsageMiddleware

__all__ = ["ALLOWANCE_USED", "INTERRUPTED", "ConversationMiddleware", "PlanToolsMiddleware", "UsageMiddleware",
           "recent_history", "tool_failed"]
