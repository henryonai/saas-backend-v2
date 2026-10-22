from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from assistant.context import AgentContext
from assistant.service import UsageService


def make_usage_tool(usage: UsageService) -> BaseTool:
    @tool
    def assistant_usage(runtime: ToolRuntime[AgentContext]) -> dict:
        """How many assistant messages, tokens and terminal sessions the workspace has used this month,
        and its plan's limits."""
        return usage.summary(runtime.context.user)

    return assistant_usage
