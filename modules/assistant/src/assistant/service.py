import json
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from aws_lambda_powertools import Logger

from shared.auth import CurrentUser
from shared.errors import Conflict, InvalidRequest, NotFound, PlanLimitReached, TooManyRequests
from shared.plans import get_limit

from assistant.agent_names import agent_names
from assistant.context import SESSION_ID, AgentContext, actor_of, checkpoint_session
from assistant.models import Conversation, Rating, Run, Usage, current_month, iso, now_iso
from assistant.repository import ConversationRepository, InvalidCursor, UsageLimitReached, UsageRepository

logger = Logger()

MESSAGES = "assistant_messages_per_month"
MESSAGES_PER_HOUR = "assistant_messages_per_user_hour"
TOKENS = "assistant_tokens_per_month"
SANDBOX_SESSIONS = "assistant_sandbox_sessions_per_month"


class UsageService:
    """How much a workspace may use the assistant: the plan rules from shared/plans.py."""

    def __init__(self, usage: UsageRepository):
        self.usage = usage

    def start_message(self, user: CurrentUser) -> Usage:
        """
        Call before running the agent: counts the message, or raises
        TooManyRequests (429) when the user has sent the plan's messages for
        this hour, or PlanLimitReached (402) when the workspace has no
        messages or tokens left this month. The hour comes first: one user
        can't use up the workspace's month in an afternoon, and a message it
        refuses doesn't count against the month. The plan comes from the
        login token, like in orders.
        """
        tenant_id = user.require_tenant()
        self._start_hourly_message(user)
        max_messages, max_tokens = get_limit(user.plan, MESSAGES), get_limit(user.plan, TOKENS)
        try:
            return self.usage.add_message(tenant_id, current_month(), max_messages, max_tokens)
        except UsageLimitReached as e:
            if max_messages is not None and e.usage.messages >= max_messages:
                raise PlanLimitReached(f"Your {user.plan} plan includes {max_messages} assistant messages "
                                       "a month. Upgrade for more.")
            raise PlanLimitReached(f"Your workspace has used this month's assistant allowance on the "
                                   f"{user.plan} plan. Upgrade for more.")

    def _start_hourly_message(self, user: CurrentUser) -> None:
        """
        A fixed window, the calendar hour in UTC, as one counter per user and
        hour: at most twice the limit can fit in an hour that straddles two.
        """
        per_hour = get_limit(user.plan, MESSAGES_PER_HOUR)
        if per_hour is None:
            return
        now = datetime.now(UTC)
        hour_ends = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if not self.usage.add_hourly_message(user.require_tenant(), user.user_id, now.strftime("%Y-%m-%dT%H"),
                                             per_hour, expires_at=int(hour_ends.timestamp()) + 3600):
            logger.info("Hourly message limit reached", extra={"limit": per_hour, "plan": user.plan})
            minutes = max(1, math.ceil((hour_ends - now).total_seconds() / 60))
            raise TooManyRequests(f"You can send the assistant {per_hour} messages an hour. Try again in "
                                  f"{minutes} minute{'s' if minutes != 1 else ''}.")

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
# AgentCore ends a streaming request after 60 minutes: a run started longer
# ago isn't answering any more (its runtime stopped before it could say so).
RUN_LIMIT = timedelta(minutes=60)


def first_message_title(prompt: str) -> str:
    """A conversation's title until the model writes one: its first message, on one line."""
    text = " ".join(prompt.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


def active_run(conversation: Conversation, now: datetime | None = None) -> Run | None:
    """The run still answering the conversation's newest message, if any: its answer isn't in the history yet."""
    run = conversation.run
    if run is None or (now or datetime.now(UTC)) - datetime.fromisoformat(run.since) >= RUN_LIMIT:
        return None
    return run


def _expires_at(days: int) -> int:
    """
    When a conversation whose last message is now expires, whole (its META's
    TTL): as long as the agents' checkpoints written now (container.py).
    """
    return int(time.time()) + days * 24 * 3600


class ConversationLog:
    """
    The runtime's side of a conversation's history (the agent itself
    remembers through its checkpoints): the run it is to answer, whether the
    user stopped it, and what streamed back, kept as the AG-UI messages the
    page showed. The user's message was saved when it was sent
    (ConversationService.send).
    """

    def __init__(self, conversations: ConversationRepository, days: int):
        self.conversations = conversations
        self.days = days

    def pending_run(self, context: AgentContext, run_id: str) -> Run | None:
        """The conversation's run, if it is `run_id` and still to be answered."""
        conversation = self.conversations.get(context.user.require_tenant(), context.user.user_id,
                                              context.session_id)
        run = conversation and active_run(conversation)
        return run if run and run.run_id == run_id else None

    def cancelled(self, context: AgentContext, run_id: str) -> bool:
        """Whether the user stopped the run, or deleted the conversation: nobody wants the rest."""
        conversation = self.conversations.get(context.user.require_tenant(), context.user.user_id,
                                              context.session_id)
        return conversation is None or conversation.run is None or conversation.run.run_id != run_id \
            or conversation.run.cancelled

    def record(self, context: AgentContext, run_id: str, messages: list[dict]) -> bool:
        """
        After the run (answered, failed, stopped or cut off): the messages it
        streamed, and that it's over. False if the conversation was deleted
        meanwhile: its answer is not kept.
        """
        saved = self.conversations.add_answer(
            context.user.require_tenant(), context.user.user_id, context.session_id, run_id,
            [_kept(message) for message in messages], now=now_iso(), expires_at=_expires_at(self.days))
        if not saved:
            logger.info("The conversation was deleted while the agent answered; its answer is not kept")
        return saved

    def name(self, context: AgentContext, title: str) -> bool:
        """Sets the model's title, unless the user renamed the conversation meanwhile."""
        return self.conversations.set_title(
            context.user.require_tenant(), context.user.user_id, context.session_id,
            title[:TITLE_CHARS], source="model", only_over="message")


def _kept(message: dict) -> dict:
    """A message as the history keeps it: a tool's long output is cut, like the bash tool cuts its own."""
    content = message.get("content")
    if message.get("role") == "tool" and isinstance(content, str) and len(content) > TOOL_RESULT_CHARS:
        return {**message, "content": content[:TOOL_RESULT_CHARS] + "\n… (cut)"}
    return message


class PurgeQueue(Protocol):
    def send(self, threads: list[str]) -> None:
        """Asks the purge worker to delete these checkpoint threads (purge.py)."""
        ...


class SqsPurgeQueue:
    def __init__(self, client, queue_url: str):
        self.client = client   # boto3 "sqs"
        self.queue_url = queue_url

    def send(self, threads: list[str]) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps({"threads": threads}))


NOT_FOUND = "No such conversation: it was deleted, or it expired."
STILL_ANSWERING = "The assistant is still answering in this conversation: wait for the answer, or stop it."


class ConversationService:
    """
    The user's conversations, for the HTTP API: send a message (or edit one),
    stop its answer, rate it, list, open, rename, delete.
    """

    def __init__(self, conversations: ConversationRepository, purge: PurgeQueue, usage: UsageService, days: int):
        self.conversations = conversations
        self.purge = purge
        self.usage = usage
        self.days = days

    def send(self, user: CurrentUser, thread_id: str, message_id: str, content: str, agent: str,
             replaces: str | None = None) -> Run:
        """
        Saves the user's message, and the run that will answer it, before
        anything runs: the conversation has them from now on, whatever the
        page does next. Counts the message against the plan (402, or 429 at
        the user's hourly limit); one answer at a time per conversation
        (409). The page then has AgentCore run it.

        The message is the newest, or it takes the place of an earlier
        message of the user's and everything after it, as in ChatGPT and the
        Vercel chatbot: `replaces` names the message edited, and the same
        message sent again (Try again, or the answer asked for again) replaces
        itself. The agent goes back to before it too (Run.rewind_to).
        """
        tenant_id = user.require_tenant()
        if not SESSION_ID.fullmatch(thread_id):
            raise InvalidRequest("Use a threadId of 33 to 100 letters, digits, '-' or '_' (for example a UUID).")
        if agent not in agent_names():
            raise NotFound(f"No agent named {agent!r}. Agents: {', '.join(sorted(agent_names()))}.")
        conversation = self.conversations.get(tenant_id, user.user_id, thread_id, expired=True)
        if conversation and conversation.expires_at <= time.time():
            # Expired, and waiting for TTL to remove it: a message now would bring its old messages back.
            raise NotFound(NOT_FOUND)
        if conversation and active_run(conversation):
            raise Conflict(STILL_ANSWERING)
        if replaces is None and conversation and conversation.question_id == message_id:
            replaces = message_id
        position = None
        if replaces is not None:
            found = conversation and self.conversations.find_message(
                tenant_id, user.user_id, thread_id, replaces, last=conversation.message_count)
            if not found or found[1] != "user":
                raise NotFound("No such message of yours in this conversation.")
            position = found[0]
        self.usage.start_message(user)
        now = datetime.now(UTC)
        first = conversation is None or (position == 1 and conversation.title_source == "message")
        run = Run(run_id=str(uuid4()), message_id=message_id, prompt=content, agent=agent, since=iso(now),
                  first=first, rewind_to=replaces)
        saved = self.conversations.add_question(
            tenant_id, user.user_id, thread_id, run=run, title=first_message_title(content),
            message={"id": message_id, "role": "user", "content": content}, at=position,
            count=conversation.message_count if conversation else 0,
            now=run.since, expires_at=_expires_at(self.days), stale_before=iso(now - RUN_LIMIT))
        if not saved:
            raise Conflict(STILL_ANSWERING)   # sent from another tab a moment ago
        return run

    def cancel(self, user: CurrentUser, thread_id: str, run_id: str) -> None:
        """
        Stops the run: it ends at its next chunk and keeps what it wrote. A
        Stop that comes after the answer ended is late, not wrong.
        """
        self._get(user, thread_id)
        self.conversations.cancel(user.require_tenant(), user.user_id, thread_id, run_id)

    def list_mine(self, user: CurrentUser, limit: int,
                  cursor: str | None = None) -> tuple[list[Conversation], str | None]:
        """Newest first, with a cursor for the next page (None at the end)."""
        try:
            return self.conversations.list(user.require_tenant(), user.user_id, limit, cursor)
        except InvalidCursor:
            raise InvalidRequest("Pass the next_cursor of the previous page as ?cursor=.")

    def get(self, user: CurrentUser, thread_id: str) -> tuple[Conversation, list[dict], dict[str, Rating]]:
        """The conversation, its latest messages (AG-UI messages, oldest first) and the user's ratings of them."""
        conversation = self._get(user, thread_id)
        messages, ratings = self.conversations.history(user.require_tenant(), user.user_id, thread_id,
                                                       MESSAGES_SHOWN, last=conversation.message_count)
        return conversation, messages, ratings

    def rate(self, user: CurrentUser, thread_id: str, message_id: str, rating: Rating | None) -> None:
        """
        Thumbs up or down on an answer (its last assistant message), or None
        to take it back. Logged too, for Logs Insights: which answers users
        like, by agent and workspace.
        """
        conversation = self._get(user, thread_id)
        found = self.conversations.find_message(user.require_tenant(), user.user_id, thread_id, message_id,
                                                last=conversation.message_count)
        if not found or found[1] != "assistant" or not self.conversations.rate(
                user.require_tenant(), user.user_id, thread_id, found[0], rating):
            raise NotFound("No such answer in this conversation.")
        logger.info("Answer rated", extra={"session_id": thread_id, "message_id": message_id, "rating": rating,
                                           "agents": sorted(conversation.agents)})

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
        Gone from the list at once; the agents' own copy (each one's
        checkpoints) goes in the background. The purge is queued first: if
        deleting the history then fails, the user deletes again, and the purge
        runs again, harmlessly.
        """
        conversation = self._get(user, thread_id)
        actor_id = actor_of(user)
        if threads := [checkpoint_session(actor_id, thread_id, agent) for agent in sorted(conversation.agents)]:
            self.purge.send(threads)
        self.conversations.delete(user.require_tenant(), user.user_id, thread_id)

    def _get(self, user: CurrentUser, thread_id: str) -> Conversation:
        conversation = None
        if SESSION_ID.fullmatch(thread_id):
            conversation = self.conversations.get(user.require_tenant(), user.user_id, thread_id)
        if conversation is None:
            raise NotFound(NOT_FOUND)
        return conversation
