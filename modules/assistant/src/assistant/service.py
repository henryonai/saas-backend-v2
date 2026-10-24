import json
import time
from typing import Protocol

from aws_lambda_powertools import Logger

from shared.auth import CurrentUser
from shared.errors import InvalidRequest, NotFound, PlanLimitReached
from shared.plans import get_limit

from assistant.context import SESSION_ID, AgentContext, actor_of, checkpoint_session
from assistant.models import Conversation, Usage, current_month, now_iso
from assistant.repository import ConversationRepository, InvalidCursor, UsageLimitReached, UsageRepository

logger = Logger()

MESSAGES = "assistant_messages_per_month"
TOKENS = "assistant_tokens_per_month"
SANDBOX_SESSIONS = "assistant_sandbox_sessions_per_month"


class UsageService:
    """How much a workspace may use the assistant: the plan rules from shared/plans.py."""

    def __init__(self, usage: UsageRepository):
        self.usage = usage

    def start_message(self, user: CurrentUser) -> Usage:
        """
        Call before running the agent: counts the message, or raises
        PlanLimitReached (402) when the workspace has no messages or tokens
        left this month. The plan comes from the login token, like in orders.
        """
        tenant_id = user.require_tenant()
        max_messages, max_tokens = get_limit(user.plan, MESSAGES), get_limit(user.plan, TOKENS)
        try:
            return self.usage.add_message(tenant_id, current_month(), max_messages, max_tokens)
        except UsageLimitReached as e:
            if max_messages is not None and e.usage.messages >= max_messages:
                raise PlanLimitReached(f"Your {user.plan} plan includes {max_messages} assistant messages "
                                       "a month. Upgrade for more.")
            raise PlanLimitReached(f"Your workspace has used this month's assistant allowance on the "
                                   f"{user.plan} plan. Upgrade for more.")

    def within_token_cap(self, user: CurrentUser) -> bool:
        """Whether the workspace has tokens left this month (checked before a subagent starts)."""
        max_tokens = get_limit(user.plan, TOKENS)
        return max_tokens is None or self.usage.get(user.require_tenant(), current_month()).tokens < max_tokens

    def record_tokens(self, user: CurrentUser, input_tokens: int, output_tokens: int) -> bool:
        """Adds one model call's tokens. Returns False once the workspace is at its token cap."""
        usage = self.usage.add_tokens(user.require_tenant(), current_month(), input_tokens, output_tokens)
        max_tokens = get_limit(user.plan, TOKENS)
        return max_tokens is None or usage.tokens < max_tokens

    def start_sandbox(self, user: CurrentUser) -> None:
        """Call before starting a sandbox (the bash tool's terminal). Raises PlanLimitReached (402)."""
        max_sessions = get_limit(user.plan, SANDBOX_SESSIONS)
        if max_sessions == 0:
            raise PlanLimitReached(f"The assistant's terminal is not included in the {user.plan} plan. "
                                   "An owner can upgrade to use it.")
        try:
            self.usage.add_sandbox_session(user.require_tenant(), current_month(), max_sessions)
        except UsageLimitReached:
            raise PlanLimitReached(f"Your {user.plan} plan includes {max_sessions} assistant terminal "
                                   "sessions a month. Upgrade for more.")

    @staticmethod
    def includes_sandbox(user: CurrentUser) -> bool:
        return get_limit(user.plan, SANDBOX_SESSIONS) != 0

    def summary(self, user: CurrentUser) -> dict:
        """This month's usage next to the plan's limits (None = unlimited)."""
        usage = self.usage.get(user.require_tenant(), current_month())
        return {"month": usage.month, "plan": user.plan,
                "messages": usage.messages, "messages_limit": get_limit(user.plan, MESSAGES),
                "tokens": usage.tokens, "tokens_limit": get_limit(user.plan, TOKENS),
                "sandbox_sessions": usage.sandbox_sessions,
                "sandbox_sessions_limit": get_limit(user.plan, SANDBOX_SESSIONS)}


TITLE_CHARS = 80               # a conversation's title, however it was set
TOOL_RESULT_CHARS = 10_000     # of each tool result kept for the history (bash cuts its own output to this)
MESSAGES_SHOWN = 200           # latest messages returned when a conversation is opened


def first_message_title(prompt: str) -> str:
    """A conversation's title until the model writes one: its first message, on one line."""
    text = " ".join(prompt.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


class ConversationLog:
    """
    Keeps the history of each conversation as the agent answers (the agent
    itself remembers through its checkpoints): the user's message, then what
    streamed back, as the AG-UI messages the page showed. What the HTTP API
    reads back when a user opens a conversation.
    """

    def __init__(self, conversations: ConversationRepository, days: int):
        self.conversations = conversations
        self.days = days   # how long the history is kept: as long as AgentCore Memory keeps the agent's copy

    def start(self, context: AgentContext, agent: str, message_id: str, prompt: str) -> None:
        """Before the agent runs: creates the conversation the first time, and saves the user's message."""
        self.conversations.start(
            context.user.require_tenant(), context.user.user_id, context.session_id, agent=agent,
            title=first_message_title(prompt), message={"id": message_id, "role": "user", "content": prompt},
            now=now_iso(), expires_at=self._expires_at())

    def record(self, context: AgentContext, messages: list[dict]) -> None:
        """After the agent ran (or failed, or was cut off): the messages it streamed."""
        saved = self.conversations.add_messages(
            context.user.require_tenant(), context.user.user_id, context.session_id,
            [_kept(message) for message in messages], now=now_iso(), expires_at=self._expires_at())
        if not saved:
            logger.info("The conversation was deleted while the agent answered; its answer is not kept")

    def name(self, context: AgentContext, title: str) -> bool:
        """Sets the model's title, unless the user renamed the conversation meanwhile."""
        return self.conversations.set_title(
            context.user.require_tenant(), context.user.user_id, context.session_id,
            title[:TITLE_CHARS], source="model", only_over="message")

    def _expires_at(self) -> int:
        return int(time.time()) + self.days * 24 * 3600


def _kept(message: dict) -> dict:
    """A message as the history keeps it: a tool's long output is cut, like the bash tool cuts its own."""
    content = message.get("content")
    if message.get("role") == "tool" and isinstance(content, str) and len(content) > TOOL_RESULT_CHARS:
        return {**message, "content": content[:TOOL_RESULT_CHARS] + "\n… (cut)"}
    return message


class PurgeQueue(Protocol):
    def send(self, actor_id: str, session_ids: list[str]) -> None:
        """Asks the purge worker to delete these AgentCore Memory sessions' events."""
        ...


class SqsPurgeQueue:
    def __init__(self, client, queue_url: str):
        self.client = client   # boto3 "sqs"
        self.queue_url = queue_url

    def send(self, actor_id: str, session_ids: list[str]) -> None:
        self.client.send_message(QueueUrl=self.queue_url,
                                 MessageBody=json.dumps({"actor_id": actor_id, "session_ids": session_ids}))


NOT_FOUND = "No such conversation: it was deleted, or it expired."


class ConversationService:
    """The user's conversations, for the HTTP API: list, open, rename, delete."""

    def __init__(self, conversations: ConversationRepository, purge: PurgeQueue):
        self.conversations = conversations
        self.purge = purge

    def list_mine(self, user: CurrentUser, limit: int,
                  cursor: str | None = None) -> tuple[list[Conversation], str | None]:
        """Newest first, with a cursor for the next page (None at the end)."""
        try:
            return self.conversations.list(user.require_tenant(), user.user_id, limit, cursor)
        except InvalidCursor:
            raise InvalidRequest("Pass the next_cursor of the previous page as ?cursor=.")

    def get(self, user: CurrentUser, thread_id: str) -> tuple[Conversation, list[dict]]:
        """The conversation and its latest messages (AG-UI messages, oldest first)."""
        conversation = self._get(user, thread_id)
        return conversation, self.conversations.messages(user.require_tenant(), user.user_id, thread_id,
                                                         MESSAGES_SHOWN)

    def rename(self, user: CurrentUser, thread_id: str, title: str) -> Conversation:
        title = " ".join(title.split())
        if not title:
            raise InvalidRequest("Give the conversation a title.")
        self._get(user, thread_id)
        if not self.conversations.set_title(user.require_tenant(), user.user_id, thread_id, title[:TITLE_CHARS],
                                            source="user"):
            raise NotFound(NOT_FOUND)
        return self._get(user, thread_id)

    def delete(self, user: CurrentUser, thread_id: str) -> None:
        """
        Gone from the list at once; the agent's own copy in AgentCore Memory
        (the exchanges and each agent's checkpoints) goes in the background.
        The purge is queued first: if deleting the history then fails, the
        user deletes again, and the purge runs again, harmlessly.
        """
        conversation = self._get(user, thread_id)
        actor_id = actor_of(user)
        self.purge.send(actor_id, [thread_id, *(checkpoint_session(actor_id, thread_id, agent)
                                                for agent in sorted(conversation.agents))])
        self.conversations.delete(user.require_tenant(), user.user_id, thread_id)

    def _get(self, user: CurrentUser, thread_id: str) -> Conversation:
        conversation = None
        if SESSION_ID.fullmatch(thread_id):
            conversation = self.conversations.get(user.require_tenant(), user.user_id, thread_id)
        if conversation is None:
            raise NotFound(NOT_FOUND)
        return conversation
