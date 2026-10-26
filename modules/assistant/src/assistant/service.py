import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from aws_lambda_powertools import Logger

from shared.auth import CurrentUser
from shared.errors import Conflict, InvalidRequest, NotFound, PlanLimitReached, TooManyRequests
from shared.plans import get_limit

from assistant.agent_names import MAIN_AGENT, agent_names
from assistant.context import SESSION_ID, AgentContext, actor_of, checkpoint_session
from assistant.models import Conversation, Memory, Rating, Run, Usage, current_month, iso, now_iso
from assistant.repository import (
    ConversationRepository,
    InvalidCursor,
    MemoryRepository,
    UsageLimitReached,
    UsageRepository,
    earlier_cursor,
    read_earlier_cursor,
)
from assistant.versions import Tree

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


MAX_MEMORIES = 100   # a user's memories: an agent with `memory: user` is sent all of them, each message
MEMORY_CHARS = 500   # each


class MemoryService:
    """
    What the assistant remembers about each user across conversations, as
    ChatGPT's saved memories: an agent with `memory: user` (its Markdown
    file) is sent all of them, and saves, corrects and forgets them with its
    tools; the user sees, edits and deletes them, or turns memory off (the
    HTTP API). Kept until the user deletes them, or leaves the workspace.
    Their text is never logged.
    """

    def __init__(self, memories: MemoryRepository):
        self.memories = memories

    def read(self, user: CurrentUser) -> tuple[bool, list[Memory]]:
        """Whether the user has memory on, and what is remembered, oldest first."""
        return self.memories.read(user.require_tenant(), user.user_id)

    def remember(self, user: CurrentUser, text: str, *, thread_id: str | None = None, replaces: str | None = None,
                 by_agent: bool = False) -> Memory:
        """
        Saves a memory (`by_agent`: an agent saved it, in conversation
        `thread_id`; else the user wrote it), or, with `replaces`, writes it
        over that one. Raises InvalidRequest (empty, too long), Conflict
        (memory off, for an agent; the user has MAX_MEMORIES) or NotFound
        (no memory `replaces`). The same text again is the same memory.
        """
        tenant_id = user.require_tenant()
        text = " ".join(text.split())
        if not text:
            raise InvalidRequest("Write what to remember.")
        if len(text) > MEMORY_CHARS:
            raise InvalidRequest(f"A memory is at most {MEMORY_CHARS} characters: keep it to one fact.")
        enabled, memories = self.memories.read(tenant_id, user.user_id)
        if by_agent and not enabled:
            raise Conflict("Memory is off: the user turned it off, so nothing is saved.")
        now = now_iso()
        if replaces is not None:
            memory = self.memories.edit(tenant_id, user.user_id, replaces, text, now)
            if memory is None:
                raise NotFound(f"No memory {replaces!r}.")
        elif same := next((m for m in memories if m.text.casefold() == text.casefold()), None):
            return same
        else:
            if len(memories) >= MAX_MEMORIES:
                raise Conflict(f"There are {MAX_MEMORIES} memories, the most kept: delete some first.")
            memory = Memory(memory_id=uuid4().hex[:8], text=text, created_at=now, updated_at=now,
                            thread_id=thread_id)
            self.memories.add(tenant_id, user.user_id, memory)
        logger.info("Memory saved", extra={"memory_id": memory.memory_id, "by": "agent" if by_agent else "user",
                                           "session_id": thread_id, "replaced": replaces is not None})
        return memory

    def forget(self, user: CurrentUser, memory_id: str, *, by_agent: bool = False) -> None:
        """Deletes one memory. Raises NotFound."""
        if not self.memories.delete(user.require_tenant(), user.user_id, memory_id):
            raise NotFound(f"No memory {memory_id!r}.")
        logger.info("Memory deleted", extra={"memory_id": memory_id, "by": "agent" if by_agent else "user"})

    def forget_all(self, user: CurrentUser) -> int:
        """Deletes every memory (memory stays on or off)."""
        deleted = self.memories.delete_all(user.require_tenant(), user.user_id)
        logger.info("All memories deleted", extra={"memories": deleted})
        return deleted

    def set_enabled(self, user: CurrentUser, enabled: bool) -> None:
        """Memory on or off. Off, agents neither see nor save memories; what is saved stays until deleted."""
        self.memories.set_enabled(user.require_tenant(), user.user_id, enabled)


TITLE_CHARS = 80               # a conversation's title, however it was set
TOOL_RESULT_CHARS = 10_000     # of each tool result kept for the history (bash cuts its own output to this)
MESSAGES_SHOWN = 200           # latest messages returned when a conversation is opened (and most per page before them)
# The most messages an agent that starts again from the history gets (its
# latest): far more than each model call is sent (factory.MAX_HISTORY_TOKENS).
RESTORED_MESSAGES = 400
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


def _expires_at(days: int) -> int | None:
    """
    When a conversation whose last message is now expires, whole (its META's
    TTL), or None to keep it until its user deletes it (days = 0).
    """
    return int(time.time()) + days * 24 * 3600 if days else None


def _expired(conversation: Conversation) -> bool:
    return conversation.expires_at is not None and conversation.expires_at <= time.time()


class ConversationLog:
    """
    The runtime's side of a conversation's history (the agent itself
    remembers through its checkpoints): the run it is to answer, whether the
    user stopped it, and what streamed back, kept as the AG-UI messages the
    page showed. The user's message was saved when it was sent
    (ConversationService.send). Each message is saved with the agent it was
    said to or by, so an agent that lost its checkpoints gets its own side of
    the conversation back (earlier).
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

    def earlier(self, context: AgentContext, run: Run) -> list[dict]:
        """
        What the run's agent was told and said in this conversation, on the
        path it shows, before the run's message, oldest first (its latest
        RESTORED_MESSAGES, from a user message): what its checkpoint would
        have, for an agent that has none any more, or whose checkpoint is of
        another path (app.restored). Messages saved before each was marked
        with its agent (October 2026) count as the main agent's.
        """
        tenant_id, user_id = context.user.require_tenant(), context.user.user_id
        conversation = self.conversations.get(tenant_id, user_id, context.session_id, expired=True)
        if conversation is None or conversation.message_count <= 1:   # the run's message is its first
            return []
        said = self.conversations.said(tenant_id, user_id, context.session_id, conversation.tree(),
                                       RESTORED_MESSAGES)
        mine = [message for agent, message in said if (agent or MAIN_AGENT) == run.agent]
        at = next((i for i, m in enumerate(mine) if m.get("role") == "user" and m.get("id") == run.message_id),
                  len(mine))
        start = next((i for i, m in enumerate(mine) if m.get("role") == "user"), at)   # what a cut left alone
        return mine[start:at]

    def record(self, context: AgentContext, run_id: str, messages: list[dict], *, agent: str,
               checkpointed: bool = False) -> bool:
        """
        After the run (answered, failed, stopped or cut off): the messages it
        streamed, as `agent`'s, and that it's over. `checkpointed`: the
        agent's checkpoint has all of it. False if the conversation was
        deleted meanwhile: its answer is not kept.
        """
        saved = self.conversations.add_answer(
            context.user.require_tenant(), context.user.user_id, context.session_id, run_id,
            [_kept(message) for message in messages], agent=agent, now=now_iso(),
            expires_at=_expires_at(self.days), checkpointed=checkpointed)
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


@dataclass(frozen=True)
class HistoryPage:
    """Some of the messages a conversation shows, oldest first, with the user's ratings of them."""
    messages: list[dict]
    ratings: dict[str, Rating]
    # Messages with other versions: which one each is, and of how many ((index from 1, count)), by id.
    versions: dict[str, tuple[int, int]]
    earlier: str | None   # where the messages before these start (earlier_cursor), or None: these are the first


NOT_FOUND = "No such conversation: it was deleted, or it expired."
STILL_ANSWERING = "The assistant is still answering in this conversation: wait for the answer, or stop it."
CHANGED = "The conversation shows another version now (changed in another tab): open it again."


class ConversationService:
    """
    The user's conversations, for the HTTP API: send a message (or edit one),
    stop its answer, rate it, show another version, list, open, rename,
    delete.
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

        The message is the newest, or, as in ChatGPT, a new version of an
        earlier message of the user's (`replaces`: an edit): the old version
        stays, with what came after it, and the conversation shows the new
        one (versions.py). The same message sent again (`replaces` is its
        own id, or it is the newest question: Try again, Regenerate) is
        answered again, and its old answer stays as a version. The agent goes
        back to before it too (Run.rewind_to), or, when its checkpoints are
        of another version of the conversation, starts again from the
        history of this one (Run.restore).
        """
        tenant_id = user.require_tenant()
        if not SESSION_ID.fullmatch(thread_id):
            raise InvalidRequest("Use a threadId of 33 to 100 letters, digits, '-' or '_' (for example a UUID).")
        if agent not in agent_names():
            raise NotFound(f"No agent named {agent!r}. Agents: {', '.join(sorted(agent_names()))}.")
        conversation = self.conversations.get(tenant_id, user.user_id, thread_id, expired=True)
        if conversation and _expired(conversation):
            # Expired, and waiting for TTL to remove it: a message now would bring its old messages back.
            raise NotFound(NOT_FOUND)
        if conversation and active_run(conversation):
            raise Conflict(STILL_ANSWERING)
        if replaces is None and conversation and conversation.question_id == message_id:
            replaces = message_id
        tree = conversation.tree() if conversation else Tree.load(None, None, 0)
        message: dict | None = {"id": message_id, "role": "user", "content": content}
        position = tree.count + 1
        if replaces is not None:
            found = conversation and self.conversations.find_message(tenant_id, user.user_id, thread_id, replaces,
                                                                     tree)
            if not found or found.role != "user":
                raise NotFound("No such message of yours in this conversation.")
            position = found.position
            if replaces == message_id:   # answered again: the message stays, its old answer is a version
                message, content = None, self._question(user, thread_id, tree, position) or content
                tree = tree.branched(position + 1) if tree.count > position else tree
            else:
                tree = tree.branched(position)
        if message is not None:
            tree = tree.grown(1)
        self.usage.start_message(user)
        now = datetime.now(UTC)
        # The agent's checkpoints are of the conversation as it is (or there are none yet to be wrong).
        current = conversation is None or conversation.rev is None or conversation.heads.get(agent) == conversation.rev
        first = conversation is None or (position == 1 and conversation.title_source == "message")
        run = Run(run_id=str(uuid4()), message_id=message_id, prompt=content, agent=agent, since=iso(now),
                  first=first, rewind_to=replaces if current else None, restore=not current)
        saved = self.conversations.add_question(
            tenant_id, user.user_id, thread_id, message=message, run=run, title=first_message_title(content),
            before=conversation, tree=tree, now=run.since, expires_at=_expires_at(self.days),
            stale_before=iso(now - RUN_LIMIT))
        if not saved:
            raise Conflict(STILL_ANSWERING)   # sent from another tab a moment ago
        return run

    def _question(self, user: CurrentUser, thread_id: str, tree: Tree, position: int) -> str | None:
        """The text of the user's message at `position`, as it was saved: what answering it again answers."""
        message = self.conversations.message(user.require_tenant(), user.user_id, thread_id, tree.node(position))
        content = message and message.get("content")
        return content if isinstance(content, str) else None

    def show_version(self, user: CurrentUser, thread_id: str, message_id: str,
                     version: int) -> tuple[Conversation, HistoryPage]:
        """
        Shows version `version` (from 1) of a message the conversation shows,
        and what followed it, on its newest versions from there ("‹ 2/3 ›").
        The conversation as it shows now, as opening it gives it. Not while
        an answer is coming (409).
        """
        tenant_id = user.require_tenant()
        conversation = self._get(user, thread_id)
        if active_run(conversation):
            raise Conflict(STILL_ANSWERING)
        tree = conversation.tree()
        found = self.conversations.find_message(tenant_id, user.user_id, thread_id, message_id, tree)
        if not found:
            raise NotFound("No such message in this conversation.")
        try:
            shown = tree.showing(found.position, version)
        except IndexError:
            raise NotFound(f"That message has no version {version}.")
        if not self.conversations.show(tenant_id, user.user_id, thread_id, shown, before=conversation,
                                       stale_before=iso(datetime.now(UTC) - RUN_LIMIT)):
            raise Conflict(CHANGED)
        return self.get(user, thread_id)

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

    def get(self, user: CurrentUser, thread_id: str) -> tuple[Conversation, HistoryPage]:
        """The conversation, and its latest messages (AG-UI messages, oldest first) with the user's ratings of them."""
        conversation = self._get(user, thread_id)
        return conversation, self._page(user, conversation, MESSAGES_SHOWN)

    def earlier(self, user: CurrentUser, thread_id: str, before: str, limit: int) -> HistoryPage:
        """
        The messages before a page already read (`before`: its
        earlier_cursor), as a long conversation is scrolled back: the
        newest `limit` of them, oldest first. 409 if the conversation shows
        another version of them now (switched in another tab).
        """
        try:
            position, fingerprint = read_earlier_cursor(before)
        except InvalidCursor:
            raise InvalidRequest("Pass the earlier_cursor of the messages you have as ?before=.")
        conversation = self._get(user, thread_id)
        if conversation.tree().fingerprint(position) != fingerprint:
            raise Conflict(CHANGED)
        return self._page(user, conversation, limit, before=position)

    def _page(self, user: CurrentUser, conversation: Conversation, limit: int,
              before: int | None = None) -> HistoryPage:
        tree = conversation.tree()
        messages, ratings, positions = self.conversations.history(
            user.require_tenant(), user.user_id, conversation.thread_id, tree, limit, before=before)
        at = {position: message["id"] for position, message in zip(positions, messages, strict=True)}
        versions = {at[position]: version for position, version in tree.versions(positions[0], positions[-1]).items()
                    if position in at} if positions else {}
        earlier = earlier_cursor(positions[0], tree) if positions and positions[0] > 1 else None
        return HistoryPage(messages, ratings, versions, earlier)

    def rate(self, user: CurrentUser, thread_id: str, message_id: str, rating: Rating | None) -> None:
        """
        Thumbs up or down on an answer (its last assistant message), or None
        to take it back. Logged too, for Logs Insights: which answers users
        like, by agent and workspace.
        """
        conversation = self._get(user, thread_id)
        tree = conversation.tree()
        found = self.conversations.find_message(user.require_tenant(), user.user_id, thread_id, message_id, tree)
        if not found or found.role != "assistant" or not self.conversations.rate(
                user.require_tenant(), user.user_id, thread_id, tree.node(found.position), rating):
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
