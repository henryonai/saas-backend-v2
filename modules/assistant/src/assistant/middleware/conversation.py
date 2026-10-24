from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from assistant.context import AgentContext

INTERRUPTED = "This tool call was interrupted before it returned a result."


class ConversationMiddleware(AgentMiddleware):
    """Keeps a checkpointed conversation valid and bounded (top-level agents only: subagents start fresh)."""

    def __init__(self, max_history_tokens: int):
        super().__init__()
        self.max_history_tokens = max_history_tokens

    def before_agent(self, state: AgentState, runtime: Runtime[AgentContext]) -> dict | None:
        # A message cut off while tools ran (client gone, microVM stopped, a
        # crash) leaves tool calls without results in the checkpoint. The model
        # API rejects that history, so every later message would fail: give each
        # such call a result, right after the message that made it.
        messages = state["messages"]
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        repaired: list[BaseMessage] = []
        for message in messages:
            repaired.append(message)
            if isinstance(message, AIMessage):
                repaired += [ToolMessage(INTERRUPTED, tool_call_id=call["id"], name=call["name"], status="error")
                             for call in message.tool_calls if call["id"] not in answered]
        if len(repaired) == len(messages):
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        # Every model call pays for the history it is sent; the checkpoint keeps all of it.
        return handler(request.override(messages=recent_history(request.messages, self.max_history_tokens)))


def recent_history(messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    """
    The latest messages that fit in about max_tokens, starting at a user
    message, so every tool call keeps its result. The latest user message and
    everything after it are always kept.
    """
    start, total = None, 0
    for i in range(len(messages) - 1, -1, -1):
        total += count_tokens_approximately([messages[i]])
        if isinstance(messages[i], HumanMessage):
            if start is not None and total > max_tokens:
                break
            start = i
    return messages if start is None else messages[start:]
