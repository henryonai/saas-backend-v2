from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from assistant.context import AgentContext
from assistant.memory import LongTermMemory

MEMORY_PROMPT = """

What you remember about this user from earlier conversations (it may be out of date; \
the user's words and the tools win over it):
{memories}"""


class MemoryState(AgentState):
    recalled: NotRequired[list[str]]


class MemoryMiddleware(AgentMiddleware):
    """
    Recalls what AgentCore Memory learned about the user before the first model
    call, shows it in the system prompt, and saves the exchange after the last
    (top-level agents only: subagents work on a task, not with the user).
    """
    state_schema = MemoryState

    def __init__(self, memory: LongTermMemory):
        super().__init__()
        self.memory = memory

    def before_agent(self, state: MemoryState, runtime: Runtime[AgentContext]) -> dict:
        question, _ = _this_exchange(state["messages"])
        recalled = self.memory.recall(runtime.context.actor_id, question.text) if question else []
        return {"recalled": recalled}

    def wrap_model_call(self, request: ModelRequest, handler):
        # In the system prompt, not the messages: memories are never saved into
        # the conversation, so they can't pile up turn after turn.
        if recalled := request.state.get("recalled"):
            base = request.system_message.text if request.system_message else ""
            memories = "\n".join(f"- {m}" for m in recalled)
            request = request.override(system_message=SystemMessage(base + MEMORY_PROMPT.format(memories=memories)))
        return handler(request)

    def after_agent(self, state: MemoryState, runtime: Runtime[AgentContext]) -> None:
        question, answer = _this_exchange(state["messages"])
        if question and answer:
            self.memory.remember(runtime.context.actor_id, runtime.context.session_id, question.text, answer.text)


def _this_exchange(messages: list[BaseMessage]) -> tuple[HumanMessage | None, AIMessage | None]:
    """The user's latest message and the final answer to it (None until there is one)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            answers = [m for m in messages[i + 1:] if isinstance(m, AIMessage) and m.text]
            return messages[i], (answers[-1] if answers else None)
    return None, None
