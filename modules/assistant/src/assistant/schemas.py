"""
What the agent endpoint accepts and streams back (its contract with the
frontend). Stored data is in models.py.

The response is always a stream of server-sent events, one JSON object per
`data:` line, ending with a `done` or an `error` event. Errors are events,
not HTTP status codes, because AgentCore Runtime turns any 4xx/5xx that the
agent returns into a generic 424 RuntimeClientError. Errors AgentCore raises
itself (401 for a missing or invalid token, 429 throttling) still arrive as
HTTP statuses.
"""
from typing import Literal

from pydantic import BaseModel, Field

from assistant.definitions import MAIN_AGENT


# ---------- request ----------

class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    agent: str = Field(default=MAIN_AGENT, max_length=64)   # an agent's `name` (agents/*.md)


# ---------- streamed events ----------

class TextEvent(BaseModel):
    type: Literal["text"] = "text"
    text: str  # a piece of the answer; append them in order


class ToolEvent(BaseModel):
    type: Literal["tool"] = "tool"
    name: str  # the agent used a tool (e.g. "list_orders"; "task": a subagent), for a "working..." hint


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    status: int  # same meaning as the HTTP API's statuses: 402 means "show Upgrade"
    message: str


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    agent: str
    session_id: str  # send it back as X-Amzn-Bedrock-AgentCore-Runtime-Session-Id to continue
    input_tokens: int
    output_tokens: int
