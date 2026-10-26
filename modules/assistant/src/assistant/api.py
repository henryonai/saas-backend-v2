"""
The HTTP API for the users' conversations with the assistant
(/assistant/conversations) and what it remembers about them
(/assistant/memories), a Lambda behind API Gateway like the other modules.

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

from assistant.container import get_conversation_service, get_memory_service
from assistant.schemas import (
    ConversationDetail,
    ConversationOut,
    ConversationPage,
    ConversationRename,
    MemoryIn,
    MemoryList,
    MemoryOut,
    MemorySettings,
    MessageIn,
    MessagePage,
    MessageRating,
    RunOut,
    Version,
    VersionIn,
)
from assistant.models import Conversation
from assistant.service import MAX_MEMORIES, ConversationService, HistoryPage, MemoryService, active_run

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[ConversationService, Depends(get_conversation_service)]
Memories = Annotated[MemoryService, Depends(get_memory_service)]


# ---------- routes ----------

@router.post("/assistant/conversations/<thread_id>/messages")
def send_message(thread_id: str, body: MessageIn, user: User, service: Service) -> Response[RunOut]:
    """
    Saves the user's message in the conversation (created the first time,
    with the client's threadId) and the run that will answer it. With
    `replaces`, it is a new version of that earlier message of the user's (an
    edit), or, with its own id, that message answered again; the old version
    stays. 402 at the plan's limit; 429 at the user's hourly limit; 409 while
    the conversation's previous answer is coming.
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
    return _detail(*service.get(user, thread_id))


@router.post("/assistant/conversations/<thread_id>/messages/<message_id>/version")
def show_version(thread_id: str, message_id: str, body: VersionIn, user: User, service: Service) -> ConversationDetail:
    """
    Shows another version of a message the conversation shows (an edit, or
    an answer asked for again), and what followed it: the conversation as it
    is opened now. 409 while an answer is coming.
    """
    return _detail(*service.show_version(user, thread_id, message_id, body.version))


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
    return MessagePage(messages=page.messages, ratings=page.ratings, versions=_versions(page),
                       earlier_cursor=page.earlier)


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


# ---------- memories ----------

@router.get("/assistant/memories")
def list_memories(user: User, memories: Memories) -> MemoryList:
    """What the assistant remembers about the caller, oldest first, and whether memory is on."""
    return _memories(memories, user)


@router.post("/assistant/memories")
def add_memory(body: MemoryIn, user: User, memories: Memories) -> Response[MemoryOut]:
    """The user tells it something to remember. 409 at the most kept."""
    memory = memories.remember(user, body.text)
    return Response(status_code=201, content_type=content_types.APPLICATION_JSON,
                    body=MemoryOut.model_validate(memory.model_dump()).model_dump(mode="json"))


@router.patch("/assistant/memories")
def set_memory(body: MemorySettings, user: User, memories: Memories) -> MemoryList:
    """Memory on or off: off, agents neither see nor save memories (what is saved stays until deleted)."""
    memories.set_enabled(user, body.enabled)
    return _memories(memories, user)


@router.patch("/assistant/memories/<memory_id>")
def edit_memory(memory_id: str, body: MemoryIn, user: User, memories: Memories) -> MemoryOut:
    return MemoryOut.model_validate(memories.remember(user, body.text, replaces=memory_id).model_dump())


@router.delete("/assistant/memories/<memory_id>")
def delete_memory(memory_id: str, user: User, memories: Memories):
    memories.forget(user, memory_id)
    return Response(status_code=204)


@router.delete("/assistant/memories")
def delete_memories(user: User, memories: Memories):
    """Forgets everything (memory stays on or off)."""
    memories.forget_all(user)
    return Response(status_code=204)


def _memories(memories: MemoryService, user: CurrentUser) -> MemoryList:
    enabled, items = memories.read(user)
    return MemoryList(enabled=enabled, items=[MemoryOut.model_validate(m.model_dump()) for m in items],
                      limit=MAX_MEMORIES)


def _detail(conversation: Conversation, page: HistoryPage) -> ConversationDetail:
    run = active_run(conversation)
    return ConversationDetail(**ConversationOut.of(conversation).model_dump(), run_id=run and run.run_id,
                              messages=page.messages, ratings=page.ratings, versions=_versions(page),
                              earlier_cursor=page.earlier)


def _versions(page: HistoryPage) -> dict[str, Version]:
    return {message_id: Version(index=index, count=count) for message_id, (index, count) in page.versions.items()}
