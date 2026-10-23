"""
AgentCore Runtime entry point, for the AG-UI protocol (ProtocolConfiguration
AGUI in template.yaml): POST /invocations streams AG-UI events as SSE, /ws
streams the same events over a WebSocket, GET /ping answers, all on port 8080
(the AgentCore SDK's AGUIApp). The request and events are in schemas.py and agui.py.

AgentCore verifies the caller's Cognito ID token before a request reaches
this code (inbound JWT authorizer in template.yaml) and passes the
Authorization header on (request header allowlist). So, as with API Gateway
for the HTTP API, we only read the claims. A local dev server has no
authorizer in front of it: never expose one.

Each message, in order:
  1. which agent: forwardedProps.agent (agents/*.md), "assistant" by default
  2. who is calling: the token's claims, checked like every other module does
  3. may they: start_message counts the message against the plan, or stops (402)
  4. the agent answers, with middleware for the token cap and memory, and the
     answer streams back as AG-UI events
"""
import asyncio
import base64
import contextvars
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator

from ag_ui.core import BaseEvent, RunAgentInput, RunFinishedEvent, RunStartedEvent, TokenUsage
from aws_lambda_powertools import Logger
from bedrock_agentcore import RequestContext
from bedrock_agentcore.runtime.ag_ui import AGUIApp
from langchain_core.messages import AIMessage, HumanMessage

from shared.auth import user_from_claims
from shared.errors import AppError, InvalidRequest, NotFound, Unauthorized

from assistant.agui import AgUiStream
from assistant.container import get_agents, get_usage_service
from assistant.context import AgentContext
from assistant.factory import thread_config
from assistant.schemas import chat_request, error_event, internal_error
from assistant.service import UsageService

logger = Logger()

# AgentCore Runtime wants 33+ characters; AgentCore Memory allows these, up to 100.
SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{32,99}")


def create_app(get_usage: Callable[[], UsageService], get_agents: Callable[[], dict]) -> AGUIApp:
    app = AGUIApp()

    @app.entrypoint
    async def invoke(run_input: RunAgentInput, context: RequestContext) -> AsyncIterator[BaseEvent]:
        async for event in in_worker_thread(lambda: handle(run_input, context, get_usage(), get_agents())):
            yield event

    return app


async def in_worker_thread[T](events: Callable[[], Iterator[T]]) -> AsyncIterator[T]:
    """
    Runs a synchronous generator (the agent: boto3, httpx) start to finish in
    one worker thread, and hands its items to the event loop. The loop stays
    free, so /ping keeps answering while the agent works (a blocked ping gets
    the session recycled). If the client goes away, the run still finishes,
    so its checkpoint and usage are complete.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def run() -> None:
        try:
            for item in events():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    worker = loop.run_in_executor(None, contextvars.copy_context().run, run)
    while (item := await queue.get()) is not done:
        yield item
    await worker   # re-raises what the generator raised


def handle(run_input: RunAgentInput, context: RequestContext, usage: UsageService,
           agents: dict) -> Iterator[BaseEvent]:
    thread_id, run_id = run_input.thread_id, run_input.run_id
    yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
    try:
        request = chat_request(run_input)
        if request.agent not in agents:
            raise NotFound(f"No agent named {request.agent!r}. Agents: {', '.join(sorted(agents))}.")
        agent = agents[request.agent]
        run = agent_context(context, thread_id)
        usage.start_message(run.user)
    except AppError as e:
        yield error_event(e)
        return
    except Exception:
        logger.exception("Could not start the message")
        yield internal_error()
        return

    log = {"tenant_id": run.user.tenant_id, "user_id": run.user.user_id, "session_id": run.session_id,
           "agent": request.agent}
    input_tokens = output_tokens = 0
    events = AgUiStream()
    try:
        # Subagents' own output doesn't come through here (see delegation.py), only the calling agent's.
        stream = agent.stream({"messages": [HumanMessage(request.prompt)]}, thread_config(run, request.agent),
                              context=run, stream_mode="messages")
        for message, _ in stream:
            yield from events.message(message)
            if isinstance(message, AIMessage) and message.usage_metadata:
                input_tokens += message.usage_metadata["input_tokens"]
                output_tokens += message.usage_metadata["output_tokens"]
        yield from events.close()
    except Exception:
        logger.exception("The agent failed", extra=log)
        yield internal_error()
        return

    # One line per message, with tokens: per-tenant cost in CloudWatch Logs Insights.
    # (A subagent's tokens are in the usage table, not in these numbers.)
    logger.info("Assistant answered", extra={**log, "input_tokens": input_tokens, "output_tokens": output_tokens})
    yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result={"agent": request.agent},
                           usage=[TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                                             total_tokens=input_tokens + output_tokens)])


def agent_context(context: RequestContext, thread_id: str) -> AgentContext:
    """Who is calling, and in which conversation. Raises Unauthorized, Forbidden or InvalidRequest."""
    authorization = (context.request_headers or {}).get("Authorization", "")
    user = user_from_claims(claims_from_bearer(authorization))
    user.require_tenant()
    # The conversation is the AgentCore session, which also keeps the microVM
    # and the sandbox. AgentCore always passes a session id (it makes one up if
    # the client sends none, which then wouldn't match); a local run has none.
    session_id = context.session_id or thread_id
    if not SESSION_ID.fullmatch(session_id):
        raise InvalidRequest("Use a threadId of 33 to 100 letters, digits, '-' or '_' (for example a UUID).")
    if thread_id != session_id:
        raise InvalidRequest("Send the threadId as the X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header too: "
                             "the conversation is the AgentCore session.")
    return AgentContext(user=user, authorization=authorization, session_id=session_id)


def claims_from_bearer(authorization: str) -> dict:
    """
    The claims of the JWT in "Bearer <token>". This verifies nothing: it is only
    for tokens that AgentCore's authorizer already verified.
    """
    scheme, _, token = authorization.partition(" ")
    parts = token.split(".")
    if scheme.lower() != "bearer" or len(parts) != 3:
        raise Unauthorized("Send the ID token as 'Authorization: Bearer <token>'")
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        raise Unauthorized("The token can't be read; sign in again")
    if not isinstance(claims, dict):
        raise Unauthorized("The token can't be read; sign in again")
    return claims


app = create_app(get_usage_service, get_agents)
