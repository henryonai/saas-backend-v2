"""
The HTTP API for the users' conversations with the assistant (/assistant/...),
a Lambda behind API Gateway like the other modules.

Sending a message starts here, as in ChatGPT: POST .../messages saves it with
the run that will answer it, and the page then has AgentCore Runtime run it,
streaming the answer over AG-UI (app.py). The other routes list, reopen,
rename and delete what was said, and stop an answer. A conversation's
messages are AG-UI messages, the same objects the client assembled from the
stream, so the frontend shows a reopened conversation exactly as it showed
the live one.
"""
from typing import Annotated, Any

from aws_lambda_powertools.event_handler import Depends, Response, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query
from pydantic import BaseModel, Field

from shared.auth import CurrentUser, get_current_user

from assistant.agent_names import MAIN_AGENT
from assistant.container import get_conversation_service
from assistant.models import Conversation
from assistant.service import ConversationService, active_run

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[ConversationService, Depends(get_conversation_service)]


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


class ConversationDetail(ConversationOut):
    # The run still answering the newest message: its answer isn't in
    # `messages` yet. Send it to AgentCore as the AG-UI runId to follow it.
    run_id: str | None
    # AG-UI messages (@ag-ui/client's Message: user, assistant with toolCalls,
    # tool results), oldest first; the latest 200 of a long conversation.
    messages: list[dict[str, Any]]


class RunOut(BaseModel):
    run_id: str   # send it to AgentCore as the AG-UI runId: the answer streams from there


# ---------- requests ----------

class MessageIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)        # the client's id for it: the history keeps it
    content: str = Field(min_length=1, max_length=4000)
    agent: str = Field(default=MAIN_AGENT, max_length=64)   # an agent's `name` (agents/*.md)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)   # cut to 80 characters


# ---------- routes ----------

@router.post("/assistant/conversations/<thread_id>/messages")
def send_message(thread_id: str, body: MessageIn, user: User, service: Service) -> Response[RunOut]:
    """
    Saves the user's message in the conversation (created the first time,
    with the client's threadId) and the run that will answer it. 402 at the
    plan's limit; 409 while the conversation's previous answer is coming.
    """
    run = service.send(user, thread_id, body.id, body.content, body.agent)
    return Response(status_code=201, content_type=content_types.APPLICATION_JSON,
                    body=RunOut(run_id=run.run_id).model_dump(mode="json"))


@router.post("/assistant/conversations/<thread_id>/runs/<run_id>/cancel")
def cancel_run(thread_id: str, run_id: str, user: User, service: Service):
    """Stops the answer: it ends within a second or so, keeping what it wrote. 204 even if it already ended."""
    service.cancel(user, thread_id, run_id)
    return Response(status_code=204)


@router.get("/assistant/conversations")
def list_conversations(user: User, service: Service,
                       limit: Annotated[int, Query(gt=0, le=50)] = 20,
                       cursor: Annotated[str | None, Query(max_length=400)] = None) -> ConversationPage:
    """The caller's conversations, most recently used first."""
    conversations, next_cursor = service.list_mine(user, limit, cursor)
    return ConversationPage(items=[ConversationOut.of(c) for c in conversations], next_cursor=next_cursor)


@router.get("/assistant/conversations/<thread_id>")
def get_conversation(thread_id: str, user: User, service: Service) -> ConversationDetail:
    conversation, messages = service.get(user, thread_id)
    run = active_run(conversation)
    return ConversationDetail(**ConversationOut.of(conversation).model_dump(), run_id=run and run.run_id,
                              messages=messages)


@router.patch("/assistant/conversations/<thread_id>")
def rename_conversation(thread_id: str, body: ConversationRename, user: User, service: Service) -> ConversationOut:
    return ConversationOut.of(service.rename(user, thread_id, body.title))


@router.delete("/assistant/conversations/<thread_id>")
def delete_conversation(thread_id: str, user: User, service: Service):
    """Deletes it and its messages; the agents' own copy (their checkpoints) goes in the background."""
    service.delete(user, thread_id)
    return Response(status_code=204)
