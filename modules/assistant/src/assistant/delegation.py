"""
The task tool: how an agent hands work to one of its subagents (the
`subagents:` list in its Markdown file).

As in Claude Code, the subagent starts fresh: it gets its own system prompt
and the instructions the calling agent writes, not the conversation. It works
for the same user (its tools act as that user, in the same sandbox), counts
against the same allowance, and returns its final answer as the tool's
result. It keeps no checkpoints, and its own words don't
stream to the user: the calling agent tells the user what it found.
"""
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool

from assistant.context import AgentContext
from assistant.definitions import AgentDefinition
from assistant.service import UsageService


def make_task_tool(subagents: dict[str, tuple[AgentDefinition, object]], usage: UsageService) -> BaseTool:
    """`subagents`: name -> (its definition, the agent built to run as a subagent)."""
    listing = "\n".join(f"- {definition.name}: {definition.description}" for definition, _ in subagents.values())

    @tool("task", description="Hand a self-contained task to a specialist agent and get its result. It knows "
                              "nothing of this conversation: put everything it needs in `instructions`.\n"
                              f"Agents (pass the name as `agent`):\n{listing}")
    def task(agent: str, instructions: str, runtime: ToolRuntime[AgentContext]) -> str:
        if agent not in subagents:
            return f"Error: there is no agent {agent!r}. Choose one of: {', '.join(subagents)}."
        if not usage.within_token_cap(runtime.context.user):
            return "Error: the workspace has used this month's assistant allowance, so the agent can't start."
        _, subagent = subagents[agent]
        result = subagent.invoke({"messages": [HumanMessage(instructions)]}, context=runtime.context)
        answers = [m for m in result["messages"] if isinstance(m, AIMessage) and m.text]
        return answers[-1].text if answers else f"{agent} finished without a result."

    return task
