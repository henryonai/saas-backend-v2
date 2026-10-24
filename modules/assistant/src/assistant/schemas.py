"""
What the agent endpoint accepts and streams back: the AG-UI protocol
(https://docs.ag-ui.com), its contract with the frontend. Stored data is in
models.py.

Request: AG-UI's RunAgentInput. The conversation lives on the server (AgentCore
Memory, per user), so only the newest message is read, and it must be the
user's, in text. The client's earlier messages, state, tools and context are
ignored: otherwise a client could put words in the assistant's mouth.
`forwardedProps.agent` picks an agent (agents/*.md), "assistant" by default.
The threadId is the conversation: send it as the
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header too.

Response: AG-UI events as server-sent events (agui.py), from RUN_STARTED to
RUN_FINISHED, or RUN_ERROR with one of the codes below. AgentCore's own errors
(a missing or invalid token, throttling) are RUN_ERROR events too, with an
HTTP status.

AG-UI is the only contract. Anything new the frontend needs goes out as an
AG-UI event (CUSTOM for what AG-UI has no event for), never in a format of
our own: the tests check every stream against the order AG-UI clients enforce.
"""
from ag_ui.core import RunAgentInput, RunErrorEvent
from pydantic import BaseModel, Field, ValidationError

from shared.errors import AppError, InvalidRequest

from assistant.definitions import MAIN_AGENT

# By status, like the HTTP API's errors; the names AgentCore uses for its own where they overlap.
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


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    agent: str = Field(default=MAIN_AGENT, max_length=64)   # an agent's `name` (agents/*.md)


def chat_request(run_input: RunAgentInput) -> ChatRequest:
    """The user's new message and the agent it is for. Raises InvalidRequest."""
    last = run_input.messages[-1] if run_input.messages else None
    if last is None or last.role != "user":
        raise InvalidRequest("End `messages` with the user's new message.")
    if isinstance(last.content, str):
        prompt = last.content
    elif all(part.type == "text" for part in last.content):
        prompt = "".join(part.text for part in last.content)
    else:
        raise InvalidRequest("Send the message as text only.")
    props = run_input.forwarded_props if isinstance(run_input.forwarded_props, dict) else {}
    try:
        return ChatRequest(prompt=prompt, agent=props.get("agent", MAIN_AGENT))
    except ValidationError:
        raise InvalidRequest("Send a message of 1 to 4000 characters, and optionally forwardedProps.agent "
                             "(an agent's name).")


def error_event(error: AppError) -> RunErrorEvent:
    return RunErrorEvent(code=ERROR_CODES.get(error.status_code, "VALIDATION_ERROR"), message=error.message)


def internal_error() -> RunErrorEvent:
    return RunErrorEvent(code="INTERNAL_ERROR", message="The assistant could not answer. Please try again.")
