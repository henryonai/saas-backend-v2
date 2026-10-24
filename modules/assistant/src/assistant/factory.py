"""
Builds the agents from their Markdown definitions: model, tools, middleware
and subagents. build_agents() receives every dependency, so tests build the
same agents with scripted models, an in-memory checkpointer and fakes.

Every agent can talk to users (the frontend picks one by name). An agent
listed in another's `subagents` is also built a second way, to run as a
subagent: no checkpoints and no memory, since it works on one task and
starts fresh each time.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware, ToolErrorMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from assistant.context import AgentContext
from assistant.definitions import AgentDefinition
from assistant.delegation import make_task_tool
from assistant.memory import LongTermMemory
from assistant.middleware import (
    ConversationMiddleware,
    MemoryMiddleware,
    PlanToolsMiddleware,
    TitleMiddleware,
    UsageMiddleware,
    tool_failed,
)
from assistant.service import ConversationLog, UsageService
from assistant.tools import SANDBOX_TOOLS

# Tool calls in one run, whatever the model decides (maxTurns bounds model calls).
MAX_TOOL_CALLS_PER_RUN = 15
# History sent with each model call (about 4 characters a token); older
# messages stay in the checkpoint. Long conversations cost the same per call.
MAX_HISTORY_TOKENS = 12_000


@dataclass(frozen=True)
class AgentDeps:
    make_model: Callable[[AgentDefinition], BaseChatModel]
    checkpointer: Any
    usage: UsageService
    memory: LongTermMemory
    tools: dict[str, BaseTool]   # tools.make_tools()
    history: ConversationLog
    title_model: BaseChatModel   # names conversations (TitleMiddleware): the stack's default model


def build_agents(definitions: dict[str, AgentDefinition], deps: AgentDeps) -> dict[str, Any]:
    """Every agent, by name, ready to talk to users."""
    called = {name for definition in definitions.values() for name in definition.subagents}
    as_subagent = {name: _build(definitions[name], deps, top_level=False) for name in called}
    agents = {}
    for definition in definitions.values():
        extra = []
        if definition.subagents:
            extra.append(make_task_tool({n: (definitions[n], as_subagent[n]) for n in definition.subagents},
                                        deps.usage))
        agents[definition.name] = _build(definition, deps, top_level=True, extra_tools=extra)
    return agents


def _build(definition: AgentDefinition, deps: AgentDeps, *, top_level: bool, extra_tools=()):
    # before_* hooks run in list order, after_* in reverse; the first wrap_*
    # hook wraps all the others, so UsageMiddleware sees every model call
    # exactly as the model answered it.
    middleware = [UsageMiddleware(deps.usage)]
    if top_level:
        middleware.append(ConversationMiddleware(MAX_HISTORY_TOKENS))
    middleware.append(PlanToolsMiddleware(deps.usage, SANDBOX_TOOLS))
    if top_level:
        middleware += [MemoryMiddleware(deps.memory), TitleMiddleware(deps.title_model, deps.history, deps.usage)]
    middleware += [
        ModelCallLimitMiddleware(run_limit=definition.max_turns, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS_PER_RUN, exit_behavior="continue"),
        ToolErrorMiddleware(on_error=tool_failed),
    ]
    return create_agent(
        deps.make_model(definition),
        tools=[deps.tools[name] for name in definition.tools] + list(extra_tools),
        system_prompt=definition.prompt,
        middleware=middleware,
        context_schema=AgentContext,
        # A subagent keeps nothing: False also stops it from inheriting the
        # caller's checkpointer, which would write every step to Memory.
        checkpointer=deps.checkpointer if top_level else False,
        name=definition.name,
    )


def thread_config(context: AgentContext, agent: str) -> dict:
    """
    Where `agent`'s side of the conversation is checkpointed: each agent keeps
    its own history. The thread id includes the user, so this holds with any
    checkpointer (AgentCoreMemorySaver also scopes by actor_id; DynamoDBSaver doesn't).
    """
    return {"configurable": {"thread_id": context.thread_id(agent), "actor_id": context.actor_id}}
