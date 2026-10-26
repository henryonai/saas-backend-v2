"""
What the assistant module stores: one usage record per workspace and month,
and each user's conversations with their messages. What its two endpoints
(the HTTP API and the agent's) accept and return is in schemas.py.
"""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel


def current_month() -> str:
    """The usage period: a calendar month in UTC, e.g. "2026-10"."""
    return datetime.now(UTC).strftime("%Y-%m")


def iso(moment: datetime) -> str:
    """As every time is stored: UTC, to the millisecond, so that they also sort as strings."""
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(datetime.now(UTC))


class Usage(BaseModel):
    tenant_id: str
    month: str
    messages: int = 0         # messages sent to the assistant
    tokens: int = 0           # input + output; what the plan's token cap counts
    input_tokens: int = 0
    output_tokens: int = 0
    sandbox_sessions: int = 0  # terminals the bash tool started


# Who named the conversation: its first message until the model writes a
# title, which never replaces one the user chose.
TitleSource = Literal["message", "model", "user"]

# The user's rating of an answer (thumbs up or down), on its message.
Rating = Literal["up", "down"]


class Run(BaseModel):
    """
    The answer being written to a conversation's newest message: one at a
    time. Created when the message is sent (the HTTP API), run by AgentCore,
    removed once the answer is in the history.
    """
    run_id: str                # the AG-UI runId: what the page sends AgentCore to start it, or to follow it
    message_id: str            # the user's message it answers
    prompt: str
    agent: str
    since: str
    cancelled: bool = False    # the user pressed Stop
    first: bool = False        # the conversation's first message: the runtime titles it meanwhile (titles.py)
    # An edited (or answered again) message: the agent's side of the
    # conversation goes back to before this user message, as the history did.
    rewind_to: str | None = None


class Conversation(BaseModel):
    """One user's conversation with the assistant: the threadId, which is also the AgentCore session."""
    thread_id: str
    title: str
    title_source: TitleSource
    created_at: str
    updated_at: str            # the last message
    message_count: int         # AG-UI messages stored: the user's, the assistant's, tool results
    agents: set[str] = set()   # the agents spoken to (forwardedProps.agent): each keeps its own checkpoints
    # Epoch seconds; DynamoDB's TTL removes it some time after. None: kept
    # until its user deletes it (CONVERSATION_DAYS = 0).
    expires_at: int | None = None
    question_id: str | None = None   # the newest user message's: sent again (a retry), it isn't added twice
    run: Run | None = None
