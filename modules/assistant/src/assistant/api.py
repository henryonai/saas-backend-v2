"""
The HTTP API for the users' conversations with the assistant (/assistant/...),
a Lambda behind API Gateway like the other modules. The chat itself streams
from AgentCore Runtime over AG-UI (app.py, schemas.py); these routes list,
reopen, rename and delete what was said. A conversation's messages are AG-UI
messages, the same objects the client assembled from the stream, so the
frontend shows a reopened conversation exactly as it showed the live one.
"""
from typing import Annotated, Any

from aws_lambda_powertools.event_handler import Depends, Response
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query
from pydantic import BaseModel, Field

from shared.auth import CurrentUser, get_current_user

from assistant.container import get_conversation_service
from assistant.models import Conversation
from assistant.service import ConversationService

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
    # AG-UI messages (@ag-ui/client's Message: user, assistant with toolCalls,
    # tool results), oldest first; the latest 200 of a long conversation.
    messages: list[dict[str, Any]]


# ---------- requests ----------

class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)   # cut to 80 characters


# ---------- routes ----------

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
    return ConversationDetail(**ConversationOut.of(conversation).model_dump(), messages=messages)


@router.patch("/assistant/conversations/<thread_id>")
def rename_conversation(thread_id: str, body: ConversationRename, user: User, service: Service) -> ConversationOut:
    return ConversationOut.of(service.rename(user, thread_id, body.title))


@router.delete("/assistant/conversations/<thread_id>")
def delete_conversation(thread_id: str, user: User, service: Service):
    """Deletes it and its messages; the agent's own copy in AgentCore Memory goes in the background."""
    service.delete(user, thread_id)
    return Response(status_code=204)
