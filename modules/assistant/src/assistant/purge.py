"""
The purge worker: deletes what is left of a conversation that is gone.

Three ways a conversation goes, one Lambda for all:
  - deleted by its user: the HTTP API (DELETE /assistant/conversations/{id})
    removes the history at once and queues each agent's thread in the
    checkpoint table (SQS), hundreds of records for a short conversation
    (checkpoints.py);
  - its user left the workspace, or was removed from it: accounts publishes
    MemberRemoved, which EventBridge puts on the same queue, and all their
    conversations there go, messages and threads, and what the assistant
    remembers about them;
  - expired, on a stack that keeps conversations for a while
    (CONVERSATION_DAYS): DynamoDB's TTL removes its META some days after its
    last message (repository.py), and the assistant table's stream brings
    that removal here (template.yaml filters the stream down to these): its
    messages and each agent's thread go.

One SQS message per Lambda run (BatchSize 1 in template.yaml); a failed or
timed-out run is retried, and picks up where the last one stopped, since what
it deleted is gone. So is a failed stream record. After several failures
either goes to the dead-letter queue, and an alarm fires.
"""
import json
from datetime import datetime

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response
from aws_lambda_powertools.utilities.data_classes.dynamo_db_stream_event import DynamoDBRecord, DynamoDBRecordEventName

from assistant.checkpoints import CheckpointPurge
from assistant.container import get_checkpoint_purge, get_purged_conversations, get_purged_memories
from assistant.context import checkpoint_session
from assistant.models import iso
from assistant.repository import ConversationRepository, MemoryRepository, conversation_of

logger = Logger()

# Who removed an item: DynamoDB's TTL, not a user's delete (which has no userIdentity).
TTL = {"type": "Service", "principalId": "dynamodb.amazonaws.com"}
MEMBER_REMOVED = "MemberRemoved"   # accounts' event, by name (modules never import each other)


def process_batch(event: dict, context, checkpoints: CheckpointPurge,
                  conversations: ConversationRepository | None = None,
                  memories: MemoryRepository | None = None) -> dict:
    """SQS: conversations their users deleted (the HTTP API's), and members removed (EventBridge's)."""
    def record_handler(record) -> None:
        body = json.loads(record.body)
        if body.get("detail-type") == MEMBER_REMOVED:
            purge_member(body["detail"], checkpoints, conversations, memories)
            return
        threads = body["threads"]
        deleted = sum(checkpoints.delete(thread) for thread in threads)
        logger.info("Purged a conversation's checkpoints", extra={"records": deleted, "threads": len(threads)})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS, logger=logger), context=context,   # logs each failure
    )


def purge_member(detail: dict, checkpoints: CheckpointPurge, conversations: ConversationRepository,
                 memories: MemoryRepository | None = None) -> None:
    """
    A member left their workspace, or was removed from it: their
    conversations there go, each as a user's delete would, checkpoints
    first, then what the assistant remembers about them. Only conversations
    last used before then: someone invited back since, whose event came late,
    keeps what they said since. Memories go whole (they have no time of use).
    """
    tenant_id, user_id = detail["tenant_id"], detail["user_id"]
    until = iso(datetime.fromisoformat(detail["occurred_at"]))   # as the conversations' times are stored
    count = deleted = 0
    for conversation in conversations.last_used_by(tenant_id, user_id, until):
        threads = [checkpoint_session(f"{tenant_id}:{user_id}", conversation.thread_id, agent)
                   for agent in sorted(conversation.agents)]
        deleted += sum(checkpoints.delete(thread) for thread in threads)
        conversations.delete(tenant_id, user_id, conversation.thread_id)
        count += 1
    remembered = memories.delete_all(tenant_id, user_id, settings=True) if memories else 0
    logger.info("Purged a removed member's conversations", extra={"tenant_id": tenant_id, "conversations": count,
                                                                  "records": deleted, "memories": remembered})


def process_expired(event: dict, context, checkpoints: CheckpointPurge,
                    conversations: ConversationRepository) -> dict:
    """The assistant table's stream: conversations whose TTL ran out."""
    def record_handler(record: DynamoDBRecord) -> None:
        keys = record.dynamodb.keys
        # template.yaml's filter lets only these through; anything else is left alone.
        if record.event_name != DynamoDBRecordEventName.REMOVE or keys.get("sk") != "META" \
                or record.user_identity != TTL:
            return
        tenant_id, user_id, thread_id = conversation_of(keys["pk"])
        agents = record.dynamodb.old_image.get("agents") or set()
        threads = [checkpoint_session(f"{tenant_id}:{user_id}", thread_id, agent) for agent in sorted(agents)]
        deleted = sum(checkpoints.delete(thread) for thread in threads)
        messages = conversations.delete(tenant_id, user_id, thread_id)
        logger.info("Purged an expired conversation", extra={"tenant_id": tenant_id, "records": deleted,
                                                             "threads": len(threads), "messages": messages})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.DynamoDBStreams, logger=logger), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    records = event.get("Records") or [{}]
    if records[0].get("eventSource") == "aws:dynamodb":
        return process_expired(event, context, get_checkpoint_purge(), get_purged_conversations())
    return process_batch(event, context, get_checkpoint_purge(), get_purged_conversations(), get_purged_memories())
