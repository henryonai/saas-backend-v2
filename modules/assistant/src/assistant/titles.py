"""
Names a conversation from its first message, as the Vercel chatbot does:
one small call to the stack's default model, made while the agent answers
rather than after, so the answer isn't kept waiting for it. Its tokens count
against the plan like any other. Until it lands, and if it fails, the title
is the first message; a title the user chose is never replaced.
"""
import contextvars
from concurrent.futures import Future, ThreadPoolExecutor

from aws_lambda_powertools import Logger
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from assistant.context import AgentContext
from assistant.service import TITLE_CHARS, ConversationLog, UsageService

logger = Logger()

TITLE_PROMPT = ("Write a title for the conversation this message starts: 2 to 6 words saying what the user wants, "
                "in the user's language. Reply with the title only, without quotes or a full stop.")
QUESTION_CHARS = 2000   # of the first message: enough to name it


class Titles:
    def __init__(self, model: BaseChatModel, history: ConversationLog, usage: UsageService):
        self.model = model
        self.history = history
        self.usage = usage
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="title")

    def start(self, context: AgentContext, question: str) -> Future:
        """Titles the conversation in the background; the future is done once the title is saved (or failed)."""
        return self._pool.submit(contextvars.copy_context().run, self._name, context, question)

    def _name(self, context: AgentContext, question: str) -> None:
        try:
            response = self.model.invoke([SystemMessage(TITLE_PROMPT), HumanMessage(question[:QUESTION_CHARS])])
            if usage := response.usage_metadata:
                self.usage.record_tokens(context.user, usage["input_tokens"], usage["output_tokens"])
            if title := clean_title(response.text):
                self.history.name(context, title)
        except Exception:
            logger.warning("Could not title the conversation; its first message stays the title", exc_info=True)


def clean_title(text: str) -> str:
    """The model's title on one line, without the quotes, markdown or full stop models add anyway."""
    line = next((line for line in text.splitlines() if line.strip()), "")
    line = line.strip().strip("\"'*#`“”‘’").strip().removesuffix(".").strip()
    return " ".join(line.split())[:TITLE_CHARS]
