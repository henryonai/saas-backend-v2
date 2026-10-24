"""
What the assistant module stores: one usage record per workspace and month,
and each user's conversations with their messages. What the agent endpoint
accepts and streams back is in schemas.py; the HTTP API's shapes in api.py.
"""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel


def current_month() -> str:
    """The usage period: a calendar month in UTC, e.g. "2026-10"."""
    return datetime.now(UTC).strftime("%Y-%m")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


class Conversation(BaseModel):
    """One user's conversation with the assistant: the threadId, which is also the AgentCore session."""
    thread_id: str
    title: str
    title_source: TitleSource
    created_at: str
    updated_at: str            # the last message
    message_count: int         # AG-UI messages stored: the user's, the assistant's, tool results
    agents: set[str] = set()   # the agents spoken to (forwardedProps.agent): each keeps its own checkpoints
    expires_at: int            # epoch seconds; DynamoDB's TTL removes it some time after
    answering_since: str | None = None   # while the agent answers the newest message: when it started
