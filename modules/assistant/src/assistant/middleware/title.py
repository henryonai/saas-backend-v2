from aws_lambda_powertools import Logger
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.constants import TAG_NOSTREAM
from langgraph.runtime import Runtime

from assistant.context import AgentContext
from assistant.service import TITLE_CHARS, ConversationLog, UsageService

logger = Logger()

TITLE_PROMPT = ("Write a title for this conversation: 2 to 6 words saying what the user wanted, in the user's "
                "language. Reply with the title only, without quotes or a full stop.")
EXCHANGE_CHARS = 2000   # of the question and of the answer: enough to name it


class TitleMiddleware(AgentMiddleware):
    """
    Names a conversation after its first answer, as ChatGPT and Claude do:
    one small model call, its tokens counted against the plan like any other.
    Until then, and if the call fails, the title is the first message; a
    title the user chose is never replaced (top-level agents only).
    """

    def __init__(self, model: BaseChatModel, history: ConversationLog, usage: UsageService):
        super().__init__()
        self.model = model
        self.history = history
        self.usage = usage

    def after_agent(self, state: AgentState, runtime: Runtime[AgentContext]) -> None:
        messages = state["messages"]
        if sum(isinstance(m, HumanMessage) for m in messages) != 1 or state.get("within_allowance") is False:
            return   # not the conversation's first exchange, or the allowance ran out during it
        question, answer = this_exchange(messages)
        if not (question and answer):
            return
        context = runtime.context
        try:
            response = self.model.invoke(
                [SystemMessage(TITLE_PROMPT), HumanMessage(f"User: {question.text[:EXCHANGE_CHARS]}\n\n"
                                                           f"Assistant: {answer.text[:EXCHANGE_CHARS]}")],
                config={"tags": [TAG_NOSTREAM]},   # not part of the answer: kept out of the stream to the user
            )
            if usage := response.usage_metadata:
                self.usage.record_tokens(context.user, usage["input_tokens"], usage["output_tokens"])
            if title := clean_title(response.text):
                self.history.name(context, title)
        except Exception:
            logger.warning("Could not title the conversation; its first message stays the title", exc_info=True)


def this_exchange(messages: list[BaseMessage]) -> tuple[HumanMessage | None, AIMessage | None]:
    """The user's latest message and the final answer to it (None until there is one)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            answers = [m for m in messages[i + 1:] if isinstance(m, AIMessage) and m.text]
            return messages[i], (answers[-1] if answers else None)
    return None, None


def clean_title(text: str) -> str:
    """The model's title on one line, without the quotes, markdown or full stop models add anyway."""
    line = next((line for line in text.splitlines() if line.strip()), "")
    line = line.strip().strip("\"'*#`“”‘’").strip().removesuffix(".").strip()
    return " ".join(line.split())[:TITLE_CHARS]
