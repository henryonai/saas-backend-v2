"""
What the agent endpoint accepts and streams back: the AG-UI protocol
(https://docs.ag-ui.com), its contract with the frontend. Stored data is in
models.py.

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
the codes below. AgentCore's own errors (a missing or invalid token,
throttling) are RUN_ERROR events too, with an HTTP status.

AG-UI is the only contract. Anything new the frontend needs goes out as an
AG-UI event (CUSTOM for what AG-UI has no event for), never in a format of
our own: the tests check every stream against the order AG-UI clients enforce.
"""
from ag_ui.core import RunErrorEvent

from shared.errors import AppError

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


def error_event(error: AppError) -> RunErrorEvent:
    return RunErrorEvent(code=ERROR_CODES.get(error.status_code, "VALIDATION_ERROR"), message=error.message)


def internal_error() -> RunErrorEvent:
    return RunErrorEvent(code="INTERNAL_ERROR", message="The assistant could not answer. Please try again.")
