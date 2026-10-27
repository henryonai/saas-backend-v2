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
mid-answer sends the same run again and reads it from the start, then live;
one whose stream broke reads on from where it was (resume_after).
Each request:
  1. who is calling, and in which workspace: the token's claims and
     forwardedProps.tenantId, checked like every other module does
  2. which run: one this microVM has (follow it), or the conversation's run
     still to be answered (start it, then follow it); else NOT_FOUND
  3. the run: the agent answers, with middleware for the token cap, as AG-UI
     events (a new conversation is titled meanwhile); it ends early if the
     user pressed Stop. An agent whose checkpoints of the conversation
     expired, or are of another version of it, starts again from its side
     of the history (restored)
  4. the history gets what streamed, however the run ended, and the
     conversation's run is removed; if the conversation was deleted
     meanwhile, the agent's checkpoints of it go too
"""
import asyncio
import base64
import dataclasses
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import wait
from contextlib import closing

from ag_ui.core import BaseEvent, RunAgentInput, RunFinishedEvent, RunStartedEvent, TokenUsage
from aws_lambda_powertools import Logger
from bedrock_agentcore import RequestContext
from bedrock_agentcore.runtime.ag_ui import AGUIApp
from bedrock_agentcore.runtime.models import PingStatus
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from shared.auth import user_from_claims
from shared.errors import AppError, InvalidRequest, NotFound, Unauthorized

from assistant.agui import AgUiStream, Transcript, agent_messages, error_event, internal_error, user_message
from assistant.container import get_agents, get_conversation_log, get_titles
from assistant.context import SESSION_ID, AgentContext, actor_of
from assistant.factory import thread_config
from assistant.models import Run
from assistant.runs import RunLog, Runs
from assistant.service import ConversationLog
from assistant.stop import StopModel
from assistant.titles import Titles

logger = Logger()

NO_RUN = "No such run in this conversation: it ended (read the conversation), or its message was never sent."
STOP_CHECK_SECONDS = 1.0   # how often a run asks whether the user pressed Stop
# After the answer, at most this long for the title written meanwhile (usually
# done by then): the page reads the list again when the run finishes.
TITLE_WAIT_SECONDS = 3.0


def create_app(get_agents: Callable[[], dict], get_log: Callable[[], ConversationLog],
               get_titles: Callable[[], Titles], runs: Runs | None = None) -> AGUIApp:
    app = AGUIApp()
    runs = runs if runs is not None else Runs()

    @app.entrypoint
    async def invoke(run_input: RunAgentInput, context: RequestContext) -> AsyncIterator[BaseEvent]:
        thread_id, run_id = run_input.thread_id, run_input.run_id
        try:
            caller = agent_context(context, thread_id, run_input.forwarded_props)
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
                log = runs.start(run_id, owner,
                                 lambda: handle(caller, pending, get_agents(), get_log(), get_titles()))
        async for event in log.follow(resume_after(run_input.forwarded_props, log)):
            yield event

    @app.ping
    def ping() -> PingStatus:
        # Busy while a run goes, even with nobody reading it: an idle session gets stopped.
        return PingStatus.HEALTHY_BUSY if runs.busy() else PingStatus.HEALTHY

    return app


def resume_after(forwarded_props, log: RunLog) -> int:
    """
    How many of the run's events the page has already, when its stream broke
    mid-answer: forwardedProps.resume = {"after": n, "startedAt": its
    RUN_STARTED's timestamp}. Only if that is this log (a run started again,
    in another microVM, said other things): else 0, from the start, and the
    page sees a RUN_STARTED it didn't have and takes back what it drew.
    """
    resume = forwarded_props.get("resume") if isinstance(forwarded_props, dict) else None
    if not isinstance(resume, dict):
        return 0
    after, started_at = resume.get("after"), resume.get("startedAt")
    if type(after) is not int or after < 0 or started_at is None or started_at != log.started_at:
        return 0
    return after


def handle(context: AgentContext, run: Run, agents: dict, history: ConversationLog,
           titles: Titles) -> Iterator[BaseEvent]:
    """One run, start to finish, as AG-UI events. Its history is kept however it ends."""
    thread_id, run_id = context.session_id, run.run_id
    context = dataclasses.replace(context, agent=run.agent)
    # Its timestamp tells this log of the run from another (resume_after).
    yield RunStartedEvent(thread_id=thread_id, run_id=run_id, timestamp=time.time_ns() // 1_000_000)
    # A new conversation is titled while the agent answers, not after it.
    naming = titles.start(context, run.prompt or ", ".join(a.name for a in run.attachments)) if run.first else None
    log = {"tenant_id": context.user.tenant_id, "user_id": context.user.user_id, "session_id": thread_id,
           "run_id": run_id, "agent": run.agent}
    input_tokens = output_tokens = 0
    stopped = checkpointed = False
    events, transcript, streamed, stop_model = AgUiStream(), Transcript(), Streamed(), StopModel()
    agent = config = None
    try:
        agent = agents.get(run.agent)
        if agent is None:   # the HTTP API checked it: a deploy removed it since
            raise NotFound(f"No agent named {run.agent!r}. Agents: {', '.join(sorted(agents))}.")
        stop_requested = stop_check(history, context, run_id)
        config = {**thread_config(context, run.agent), "callbacks": [stop_model]}
        # The checkpoint as saved (like keep_what_the_user_saw), or None.
        saved = agent.checkpointer.get_tuple(config)
        if saved and not run.restore:
            earlier = rewound(saved, run.rewind_to, log)
        else:   # none, or one of another version of the conversation
            earlier = restored(history, context, run, log, replace=saved is not None)
        # The user's message keeps its AG-UI id: an edit or a retry finds it (rewound).
        messages = [*earlier, user_message(run.message_id, run.prompt, run.attachments)]
        # Subagents' own output doesn't come through here (see delegation.py), only the calling agent's.
        # durability="exit": one checkpoint when the run ends (stopped, failed or done), not one per step.
        stream = agent.stream({"messages": messages}, config, context=context, stream_mode="messages",
                              durability="exit")
        with closing(stream):
            for message, _ in stream:
                streamed.add(message)
                for event in events.message(message):
                    transcript.add(event)
                    yield event
                if isinstance(message, AIMessage) and message.usage_metadata:
                    input_tokens += message.usage_metadata["input_tokens"]
                    output_tokens += message.usage_metadata["output_tokens"]
                if stop_requested():
                    stopped = True
                    stop_model.stopping.set()
                    break
        # The run's checkpoint was written as its stream closed: it has the whole run, or (stopped) is given it.
        checkpointed = keep_what_the_user_saw(agent, config, streamed.messages, log) if stopped else True
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
        if keep_history(history, context, run, transcript, log, checkpointed) is False and config is not None:
            forget(agent, config, log)

    if naming is not None:
        wait([naming], timeout=TITLE_WAIT_SECONDS)   # the conversation takes new messages meanwhile

    # A model call the user stopped has no usage_metadata: StopModel estimated its tokens.
    input_tokens += stop_model.input_tokens
    output_tokens += stop_model.output_tokens
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


class Streamed:
    """The agent's messages as they streamed: chunks joined into whole messages, in order."""

    def __init__(self):
        self._messages: dict[str, BaseMessage] = {}

    def add(self, message: BaseMessage) -> None:
        if isinstance(message, ToolMessage):
            key = f"tool:{message.tool_call_id}"
        else:
            key = f"ai:{message.id or len(self._messages)}"
        earlier = self._messages.get(key)
        if isinstance(earlier, AIMessageChunk) and isinstance(message, AIMessageChunk):
            self._messages[key] = earlier + message
        else:
            self._messages[key] = message

    @property
    def messages(self) -> list[BaseMessage]:
        return list(self._messages.values())


def rewound(saved, message_id: str | None, log: dict) -> list[BaseMessage]:
    """
    An edited message, or one sent again: the agent's side of the
    conversation (its checkpoint as `saved`) goes back to before that user
    message, as the history did (ConversationService.send). Nothing to undo
    when the agent never got the message (its run never checkpointed), or it
    predates the messages' ids (before October 2026).
    """
    if message_id is None:
        return []
    messages = saved.checkpoint["channel_values"].get("messages", [])
    at = next((i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and m.id == message_id), None)
    if at is None:
        logger.info("The agent doesn't have the message it answers again; nothing to undo", extra=log)
        return []
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages[:at]]


def restored(history: ConversationLog, context: AgentContext, run: Run, log: dict,
             replace: bool = False) -> list[BaseMessage]:
    """
    The agent has no checkpoint of the conversation: it never answered in it
    (nothing to restore), its checkpoints expired while the conversation is
    kept (CHECKPOINT_DAYS without a message), or a run died before writing
    its first. Or its checkpoint is of another version of the conversation
    (Run.restore): the user showed another one, or another agent spoke since
    (`replace`: what it has goes). It starts again from what the history has
    of its side of the conversation it shows, before this message: the same
    messages, so it remembers what the user sees.
    """
    messages = agent_messages(history.earlier(context, run))
    if messages or replace:
        logger.info("Restored the agent's side of the conversation from its history",
                    extra={**log, "messages": len(messages), "replaced": replace})
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages] if replace else messages


def keep_what_the_user_saw(agent, config: dict, streamed: list[BaseMessage], log: dict) -> bool:
    """
    Stop closes the stream in the middle of a step, and LangGraph checkpoints
    only whole steps: a tool's result that came back just before stays a
    pending write, which the next message's run discards, and the answer's
    text so far isn't kept at all. The user saw both, and the history has
    them, so they go into the agent's state too. A tool call cut short is
    left out (ConversationMiddleware answers one that never ran). False if
    that failed: the checkpoint lacks what the user saw.
    """
    try:
        # The checkpoint as saved: get_state() would add the pending writes, which the next run drops.
        saved = agent.checkpointer.get_tuple(config)
        kept = saved.checkpoint["channel_values"].get("messages", []) if saved else []
        answered = {m.tool_call_id for m in kept if isinstance(m, ToolMessage)}
        said = {m.id for m in kept if isinstance(m, AIMessage)}
        missing = []
        for message in streamed:
            if isinstance(message, ToolMessage) and message.tool_call_id not in answered:
                missing.append(message)
            elif isinstance(message, AIMessage) and message.id not in said and message.text:
                missing.append(AIMessage(content=message.text, id=message.id))
        if missing:
            agent.update_state(config, {"messages": missing}, as_node="model")
        return True
    except Exception:
        logger.warning("Could not keep what the stopped run showed; the agent goes without it", extra=log,
                       exc_info=True)
        return False


def keep_history(history: ConversationLog, context: AgentContext, run: Run, transcript: Transcript,
                 log: dict, checkpointed: bool) -> bool | None:
    """
    Best effort: the user has the answer already, and the agent its
    checkpoint (`checkpointed`: all of the run). False if the conversation
    was deleted meanwhile; None if saving failed.
    """
    try:
        return history.record(context, run.run_id, transcript.messages, agent=run.agent, checkpointed=checkpointed)
    except Exception:
        logger.exception("Could not save the conversation's history", extra=log)
        return None


def forget(agent, config: dict, log: dict) -> None:
    """
    The conversation was deleted while the agent answered. Deleting it queued
    a purge of the agent's checkpoints, which may well have run before this
    run wrote its last one (the run stops at its next chunk, or after the
    tool it is in): so the run deletes its thread itself, once it has written
    everything. Best effort: the checkpoints expire anyway.
    """
    try:
        agent.checkpointer.delete_thread(config["configurable"]["thread_id"])
    except Exception:
        logger.warning("Could not delete the deleted conversation's checkpoints; they expire on their own",
                       extra=log, exc_info=True)


def agent_context(context: RequestContext, thread_id: str, forwarded_props=None) -> AgentContext:
    """
    Who is calling, in which workspace and in which conversation. Raises
    Unauthorized, Forbidden or InvalidRequest.

    The workspace is forwardedProps.tenantId, the HTTP API's X-Tenant-Id: in
    the request body, since AgentCore passes on only allowlisted headers.
    """
    authorization = (context.request_headers or {}).get("Authorization", "")
    tenant_id = forwarded_props.get("tenantId") if isinstance(forwarded_props, dict) else None
    user = user_from_claims(claims_from_bearer(authorization), tenant_id if isinstance(tenant_id, str) else None)
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


app = create_app(get_agents, get_conversation_log, get_titles)
