"""
What the assistant's two endpoints accept and return (their contract with
the frontend). Stored data is in models.py. This file imports neither AG-UI
nor LangChain: the HTTP API's Lambda has only the shared layer.

The HTTP API (api.py, a Lambda; also the source of its OpenAPI document):
the classes below.

The agent endpoint (app.py, on AgentCore) speaks the AG-UI protocol
(https://docs.ag-ui.com).

Request: AG-UI's RunAgentInput, for its threadId and runId. The threadId is
the conversation: send it as the X-Amzn-Bedrock-AgentCore-Runtime-Session-Id
header too. The runId is the run the HTTP API gave the message when it saved
it (POST /assistant/conversations/{threadId}/messages): the message and its
agent are read from there. The client's messages, state, tools and context
are ignored: otherwise a client could put words in the assistant's mouth.
forwardedProps.resume = {"after": n, "startedAt": t} reads on after the
first n events of the run, for a client whose stream broke (t: the
RUN_STARTED timestamp it had; app.resume_after).

Response: AG-UI events as server-sent events (agui.py), from RUN_STARTED (its
timestamp: when the run started) to RUN_FINISHED, or RUN_ERROR with one of
ERROR_CODES (agui.error_event). AgentCore's own errors (a missing or invalid
token, throttling) are RUN_ERROR events too, with an HTTP status.

AG-UI is the only contract. Anything new the frontend needs goes out as an
AG-UI event (CUSTOM for what AG-UI has no event for), never in a format of
our own: the tests check every stream against the order AG-UI clients enforce.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from assistant.agent_names import MAIN_AGENT
from assistant.models import Conversation, FileKind, FileSource, FileVersion, Rating

# The agent endpoint's RUN_ERROR codes, by status, like the HTTP API's errors;
# the names AgentCore uses for its own where they overlap.
ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    402: "PLAN_LIMIT_REACHED",   # the frontend shows "Upgrade"
    403: "ACCESS_DENIED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    429: "RATE_LIMIT_EXCEEDED",
    503: "SERVICE_BUSY",
}


# ---------- requests ----------

class MessageIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)        # the client's id for it: the history keeps it
    content: str = Field(default="", max_length=4000)    # may be empty with attachments
    # The user's files it is sent with: ids from POST /assistant/files, uploaded. The message's
    # content is then AG-UI parts: its text, and an image or document part per file (a file source).
    attachments: list[str] = Field(default=[], max_length=5)
    agent: str = Field(default=MAIN_AGENT, max_length=64)   # an agent's `name` (agents/*.md)
    # An edit: the id of the user's earlier message this one is a new
    # version of; the old one stays, with what came after it. Its own id
    # (or the newest question's id sent again: Try again) answers it again,
    # and the old answer stays a version.
    replaces: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _says_something(self) -> "MessageIn":
        if not self.content.strip() and not self.attachments and self.replaces != self.id:
            raise ValueError("Write a message, or attach a file.")
        return self


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)   # cut to 80 characters


class MessageRating(BaseModel):
    rating: Rating | None   # thumbs up or down; null takes the rating back


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)   # one fact, as the user wants it remembered


class MemorySettings(BaseModel):
    enabled: bool   # off: agents neither see nor save memories; what is saved stays until deleted


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=200)   # as the browser has it; else from the name
    size: int = Field(gt=0)   # bytes: the upload must be exactly this (images up to 5 MB, other files 20 MB)


class VersionIn(BaseModel):
    version: int = Field(ge=1, le=10_000)   # which version of the message to show, from 1 ("‹ 2/3 ›")


# ---------- responses ----------

class ConversationOut(BaseModel):
    thread_id: str          # the AG-UI threadId: send it (and as the session header) to continue
    title: str
    created_at: str
    updated_at: str         # the last message
    message_count: int

    @classmethod
    def of(cls, conversation: Conversation) -> "ConversationOut":
        return cls.model_validate(conversation.model_dump())


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None = None  # pass back as ?cursor= to get the next page


class Version(BaseModel):
    index: int   # which version of the message this is, from 1
    count: int   # of how many: POST .../messages/{id}/version shows another


class MessagePage(BaseModel):
    # AG-UI messages (@ag-ui/client's Message: user, assistant with toolCalls,
    # tool results), oldest first: the path through the versions shown.
    messages: list[dict[str, Any]]
    # The user's ratings of answers, by the id of the answer's last assistant message.
    ratings: dict[str, Rating] = {}
    # Messages with other versions, by id: a user message edited, or an
    # answer's first message for an answer asked for again.
    versions: dict[str, Version] = {}
    # The messages before these, if there are any: pass it as ?before= to
    # GET /assistant/conversations/{threadId}/messages. None: these are the first.
    earlier_cursor: str | None = None


class ConversationDetail(MessagePage, ConversationOut):
    """A conversation as it is opened: the latest 200 messages of a long one (earlier_cursor for the rest)."""
    # The run still answering the newest message: its answer isn't in
    # `messages` yet. Send it to AgentCore as the AG-UI runId to follow it.
    run_id: str | None


class MemoryOut(BaseModel):
    memory_id: str
    text: str
    created_at: str
    updated_at: str
    thread_id: str | None = None   # the conversation an agent saved it in; None: the user wrote it


class MemoryList(BaseModel):
    enabled: bool             # whether agents remember things about the user
    items: list[MemoryOut]    # oldest first
    limit: int                # the most kept: an agent with memory is sent them all


class FileOut(BaseModel):
    file_id: str
    name: str
    content_type: str
    kind: FileKind           # image, text, data, document or other
    size: int                # bytes, of the newest version
    source: FileSource       # upload: the user attached it; assistant: an agent made it
    status: Literal["pending", "ready"]   # pending: uploaded, not sent with a message yet
    created_at: str
    updated_at: str
    versions: list[FileVersion]   # oldest first; an agent saving it again adds one
    thread_id: str | None = None  # the conversation it was attached to or made in
    agent: str | None = None      # the agent that made it


class FileDetail(FileOut):
    download_url: str             # a short-lived link that downloads it (the version asked for, or the newest)
    view_url: str | None = None   # for an image: a short-lived link that shows it


class FilePage(BaseModel):
    items: list[FileOut]
    next_cursor: str | None = None   # pass back as ?cursor= to get the next page


class UploadOut(BaseModel):
    file: FileOut
    # A presigned POST to S3, for 15 minutes: a multipart form with these fields, then the
    # file as the last field, "file". The file's id goes in the message's `attachments`.
    url: str
    fields: dict[str, str]


class RunOut(BaseModel):
    run_id: str   # send it to AgentCore as the AG-UI runId: the answer streams from there
