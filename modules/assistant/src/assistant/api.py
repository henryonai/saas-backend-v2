"""
The HTTP API for the users' conversations with the assistant (/assistant/...),
a Lambda behind API Gateway like the other modules.

Sending a message starts here, as in ChatGPT: POST .../messages saves it with
the run that will answer it, and the page then has AgentCore Runtime run it,
streaming the answer over AG-UI (app.py). The other routes list, reopen,
rename and delete what was said, stop an answer, and rate one. A conversation's
messages are AG-UI messages, the same objects the client assembled from the
stream, so the frontend shows a reopened conversation exactly as it showed
the live one.
"""
from typing import Annotated

from aws_lambda_powertools.event_handler import Depends, Response, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query

from shared.auth import CurrentUser, get_current_user

from assistant.container import get_conversation_service
from assistant.schemas import (
    ConversationDetail,
    ConversationOut,
    ConversationPage,
    ConversationRename,
    MessageIn,
    MessagePage,
    MessageRating,
    RunOut,
)
from assistant.service import ConversationService, active_run

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[ConversationService, Depends(get_conversation_service)]


# ---------- routes ----------

@router.post("/assistant/conversations/<thread_id>/messages")
def send_message(thread_id: str, body: MessageIn, user: User, service: Service) -> Response[RunOut]:
    """
    Saves the user's message in the conversation (created the first time,
    with the client's threadId) and the run that will answer it. With
    `replaces`, it takes the place of that earlier message of the user's and
    everything after it (an edit). 402 at the plan's limit; 429 at the user's
    hourly limit; 409 while the conversation's previous answer is coming.
    """
    run = service.send(user, thread_id, body.id, body.content, body.agent, replaces=body.replaces)
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
    """The conversation and its latest 200 messages; earlier_cursor reads back from there."""
    conversation, page = service.get(user, thread_id)
    run = active_run(conversation)
    return ConversationDetail(**ConversationOut.of(conversation).model_dump(), run_id=run and run.run_id,
                              messages=page.messages, ratings=page.ratings, earlier_cursor=page.earlier)


@router.get("/assistant/conversations/<thread_id>/messages")
def earlier_messages(thread_id: str, user: User, service: Service,
                     before: Annotated[str, Query(min_length=1, max_length=400)],
                     limit: Annotated[int, Query(gt=0, le=200)] = 100) -> MessagePage:
    """
    The messages before the ones the page has (`before`: their
    earlier_cursor), newest `limit` of them, oldest first: a long
    conversation scrolled back.
    """
    page = service.earlier(user, thread_id, before, limit)
    return MessagePage(messages=page.messages, ratings=page.ratings, earlier_cursor=page.earlier)


@router.patch("/assistant/conversations/<thread_id>/messages/<message_id>")
def rate_answer(thread_id: str, message_id: str, body: MessageRating, user: User, service: Service):
    """Rates an answer (`message_id`: its last assistant message), or takes the rating back. 204."""
    service.rate(user, thread_id, message_id, body.rating)
    return Response(status_code=204)


@router.patch("/assistant/conversations/<thread_id>")
def rename_conversation(thread_id: str, body: ConversationRename, user: User, service: Service) -> ConversationOut:
    return ConversationOut.of(service.rename(user, thread_id, body.title))


@router.delete("/assistant/conversations/<thread_id>")
def delete_conversation(thread_id: str, user: User, service: Service):
    """Deletes it and its messages; the agents' own copy (their checkpoints) goes in the background."""
    service.delete(user, thread_id)
    return Response(status_code=204)
