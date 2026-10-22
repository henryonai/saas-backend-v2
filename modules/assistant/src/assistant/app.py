"""
AgentCore Runtime entry point: serves POST /invocations and GET /ping on
port 8080 (BedrockAgentCoreApp).

AgentCore verifies the caller's Cognito ID token before a request reaches
this code (inbound JWT authorizer in template.yaml) and passes the
Authorization header on (request header allowlist). So, as with API Gateway
for the HTTP API, we only read the claims. A local dev server has no
authorizer in front of it: never expose one.

Each message, in order:
  1. which agent: the request's `agent` (agents/*.md), "assistant" by default
  2. who is calling: the token's claims, checked like every other module does
  3. may they: start_message counts the message against the plan, or stops (402)
  4. the agent answers, with middleware for the token cap and memory, and the
     answer streams back as server-sent events (schemas.py)
"""
import base64
import json
import re
from collections.abc import Callable, Iterator
from uuid import uuid4

from aws_lambda_powertools import Logger
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from pydantic import ValidationError

from shared.auth import user_from_claims
from shared.errors import AppError, InvalidRequest, NotFound, Unauthorized

from assistant.container import get_agents, get_usage_service
from assistant.context import AgentContext
from assistant.factory import thread_config
from assistant.schemas import ChatRequest, DoneEvent, ErrorEvent, TextEvent, ToolEvent
from assistant.service import UsageService

logger = Logger()

# AgentCore Runtime wants 33+ characters; AgentCore Memory allows these, up to 100.
SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{32,99}")


def create_app(get_usage: Callable[[], UsageService], get_agents: Callable[[], dict]) -> BedrockAgentCoreApp:
    app = BedrockAgentCoreApp()

    # A plain generator: the SDK runs it in a worker thread, so /ping keeps
    # answering while the agent works (a blocked ping gets the session recycled).
    @app.entrypoint
    def invoke(payload: dict, context: RequestContext) -> Iterator[dict]:
        yield from handle(payload, context, get_usage(), get_agents())

    return app


def handle(payload: dict, context: RequestContext, usage: UsageService, agents: dict) -> Iterator[dict]:
    try:
        request = ChatRequest.model_validate(payload)
    except ValidationError:
        yield ErrorEvent(status=422, message='Send {"prompt": "..."} with 1 to 4000 characters, '
                                             'and optionally "agent".').model_dump()
        return
    try:
        if request.agent not in agents:
            raise NotFound(f"No agent named {request.agent!r}. Agents: {', '.join(sorted(agents))}.")
        agent = agents[request.agent]
        run = agent_context(context)
        usage.start_message(run.user)
    except AppError as e:
        yield ErrorEvent(status=e.status_code, message=e.message).model_dump()
        return
    except Exception:
        logger.exception("Could not start the message")
        yield ErrorEvent(status=500, message="The assistant could not answer. Please try again.").model_dump()
        return

    log = {"tenant_id": run.user.tenant_id, "user_id": run.user.user_id, "session_id": run.session_id,
           "agent": request.agent}
    input_tokens = output_tokens = 0
    try:
        # Subagents' own output doesn't come through here (see delegation.py), only the calling agent's.
        stream = agent.stream({"messages": [HumanMessage(request.prompt)]}, thread_config(run, request.agent),
                              context=run, stream_mode="messages")
        for message, _ in stream:
            if not isinstance(message, AIMessage):
                continue  # tool results: the model reads them, the user sees its answer
            if message.text:
                yield TextEvent(text=message.text).model_dump()
            calls = message.tool_call_chunks if isinstance(message, AIMessageChunk) else message.tool_calls
            for call in calls:
                if call.get("name"):
                    yield ToolEvent(name=call["name"]).model_dump()
            if message.usage_metadata:
                input_tokens += message.usage_metadata["input_tokens"]
                output_tokens += message.usage_metadata["output_tokens"]
    except Exception:
        logger.exception("The agent failed", extra=log)
        yield ErrorEvent(status=500, message="The assistant could not answer. Please try again.").model_dump()
        return

    # One line per message, with tokens: per-tenant cost in CloudWatch Logs Insights.
    # (A subagent's tokens are in the usage table, not in these numbers.)
    logger.info("Assistant answered", extra={**log, "input_tokens": input_tokens, "output_tokens": output_tokens})
    yield DoneEvent(agent=request.agent, session_id=run.session_id,
                    input_tokens=input_tokens, output_tokens=output_tokens).model_dump()


def agent_context(context: RequestContext) -> AgentContext:
    """Who is calling, and in which conversation. Raises Unauthorized, Forbidden or InvalidRequest."""
    authorization = (context.request_headers or {}).get("Authorization", "")
    user = user_from_claims(claims_from_bearer(authorization))
    user.require_tenant()
    # AgentCore always passes a session id (it makes one if the client doesn't);
    # only a local run can lack one, and then each message starts a new conversation.
    session_id = context.session_id or str(uuid4())
    if not SESSION_ID.fullmatch(session_id):
        raise InvalidRequest("Use a session id of 33 to 100 letters, digits, '-' or '_' (for example a UUID).")
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
