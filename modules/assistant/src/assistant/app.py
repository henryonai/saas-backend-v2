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

A message is sent in two steps, as in ChatGPT:
  1. the HTTP API saves it, with the run that will answer it, and counts it
     against the plan (ConversationService.send): from then on the
     conversation has it, whatever the page does;
  2. the page sends that run here (AG-UI runId; threadId: the conversation).

A run goes on whatever happens to the request that started it: it writes to
a log in this microVM (runs.py), which the response reads. A page reloaded
mid-answer sends the same run again and reads it from the start, then live.
Each request:
  1. who is calling: the token's claims, checked like every other module does
  2. which run: one this microVM has (follow it), or the conversation's run
     still to be answered (start it, then follow it); else NOT_FOUND
  3. the run: the agent answers, with middleware for the token cap, memory
     and the title, as AG-UI events; it ends early if the user pressed Stop
  4. the history gets what streamed, however the run ended, and the
     conversation's run is removed
"""
import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import closing

from ag_ui.core import BaseEvent, RunAgentInput, RunFinishedEvent, RunStartedEvent, TokenUsage
from aws_lambda_powertools import Logger
from bedrock_agentcore import RequestContext
from bedrock_agentcore.runtime.ag_ui import AGUIApp
from bedrock_agentcore.runtime.models import PingStatus
from langchain_core.messages import AIMessage, HumanMessage

from shared.auth import user_from_claims
from shared.errors import AppError, InvalidRequest, NotFound, Unauthorized

from assistant.agui import AgUiStream, Transcript
from assistant.container import get_agents, get_conversation_log
from assistant.context import SESSION_ID, AgentContext, actor_of
from assistant.factory import thread_config
from assistant.models import Run
from assistant.runs import Runs
from assistant.schemas import error_event, internal_error
from assistant.service import ConversationLog

logger = Logger()

NO_RUN = "No such run in this conversation: it ended (read the conversation), or its message was never sent."
STOP_CHECK_SECONDS = 1.0   # how often a run asks whether the user pressed Stop


def create_app(get_agents: Callable[[], dict], get_log: Callable[[], ConversationLog],
               runs: Runs | None = None) -> AGUIApp:
    app = AGUIApp()
    runs = runs if runs is not None else Runs()

    @app.entrypoint
    async def invoke(run_input: RunAgentInput, context: RequestContext) -> AsyncIterator[BaseEvent]:
        thread_id, run_id = run_input.thread_id, run_input.run_id
        try:
            caller = agent_context(context, thread_id)
            if not SESSION_ID.fullmatch(run_id):
                raise InvalidRequest("Send the runId the HTTP API gave the message "
                                     "(POST /assistant/conversations/{threadId}/messages).")
        except AppError as e:
            yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
            yield error_event(e)
            return
        owner = actor_of(caller.user)
        log = runs.get(run_id, owner)
        if log is None:
            try:
                pending = await asyncio.to_thread(lambda: get_log().pending_run(caller, run_id))
            except Exception:
                logger.exception("Could not read the conversation's run")
                yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
                yield internal_error()
                return
            log = runs.get(run_id, owner)   # another request for it may have started it meanwhile
            if log is None:
                if pending is None:
                    yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
                    yield error_event(NotFound(NO_RUN))
                    return
                log = runs.start(run_id, owner, lambda: handle(caller, pending, get_agents(), get_log()))
        async for event in log.follow():
            yield event

    @app.ping
    def ping() -> PingStatus:
        # Busy while a run goes, even with nobody reading it: an idle session gets stopped.
        return PingStatus.HEALTHY_BUSY if runs.busy() else PingStatus.HEALTHY

    return app


def handle(context: AgentContext, run: Run, agents: dict, history: ConversationLog) -> Iterator[BaseEvent]:
    """One run, start to finish, as AG-UI events. Its history is kept however it ends."""
    thread_id, run_id = context.session_id, run.run_id
    yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
    log = {"tenant_id": context.user.tenant_id, "user_id": context.user.user_id, "session_id": thread_id,
           "run_id": run_id, "agent": run.agent}
    input_tokens = output_tokens = 0
    stopped = False
    events, transcript = AgUiStream(), Transcript()
    try:
        agent = agents.get(run.agent)
        if agent is None:   # the HTTP API checked it: a deploy removed it since
            raise NotFound(f"No agent named {run.agent!r}. Agents: {', '.join(sorted(agents))}.")
        stop_requested = stop_check(history, context, run_id)
        # Subagents' own output doesn't come through here (see delegation.py), only the calling agent's.
        stream = agent.stream({"messages": [HumanMessage(run.prompt)]}, thread_config(context, run.agent),
                              context=context, stream_mode="messages")
        with closing(stream):
            for message, _ in stream:
                for event in events.message(message):
                    transcript.add(event)
                    yield event
                if isinstance(message, AIMessage) and message.usage_metadata:
                    input_tokens += message.usage_metadata["input_tokens"]
                    output_tokens += message.usage_metadata["output_tokens"]
                if stop_requested():
                    stopped = True
                    break
        for event in events.close():
            transcript.add(event)
            yield event
    except AppError as e:
        yield error_event(e)
        return
    except Exception:
        logger.exception("The agent failed", extra=log)
        yield internal_error()
        return
    finally:
        # What streamed, however the run ended; before RUN_FINISHED, so a
        # client that reloads the conversation then finds all of it.
        keep_history(history, context, run_id, transcript, log)

    # One line per message, with tokens: per-tenant cost in CloudWatch Logs Insights.
    # (A subagent's tokens are in the usage table, not in these numbers.)
    logger.info("Assistant answered", extra={**log, "input_tokens": input_tokens, "output_tokens": output_tokens,
                                              "stopped": stopped})
    yield RunFinishedEvent(thread_id=thread_id, run_id=run_id,
                           result={"agent": run.agent, **({"stopped": True} if stopped else {})},
                           usage=[TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                                             total_tokens=input_tokens + output_tokens)])


def stop_check(history: ConversationLog, context: AgentContext, run_id: str) -> Callable[[], bool]:
    """
    Whether the user pressed Stop (the HTTP API marks the run) or deleted the
    conversation: asked at most once a STOP_CHECK_SECONDS, between chunks.
    """
    next_check = time.monotonic() + STOP_CHECK_SECONDS

    def stop_requested() -> bool:
        nonlocal next_check
        if time.monotonic() < next_check:
            return False
        next_check = time.monotonic() + STOP_CHECK_SECONDS
        try:
            return history.cancelled(context, run_id)
        except Exception:
            logger.warning("Could not check whether the run was stopped; it goes on", exc_info=True)
            return False

    return stop_requested


def keep_history(history: ConversationLog, context: AgentContext, run_id: str, transcript: Transcript,
                 log: dict) -> None:
    """Best effort: the user has the answer already, and the agent its checkpoint."""
    try:
        history.record(context, run_id, transcript.messages)
    except Exception:
        logger.exception("Could not save the conversation's history", extra=log)


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


app = create_app(get_agents, get_conversation_log)
