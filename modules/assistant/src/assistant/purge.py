"""
The purge worker: deletes what is left of a conversation that is gone.

Two ways a conversation goes, one Lambda for both:
  - deleted by its user: the HTTP API (DELETE /assistant/conversations/{id})
    removes the history at once and queues each agent's thread in the
    checkpoint table (SQS), hundreds of records for a short conversation
    (checkpoints.py);
  - expired: DynamoDB's TTL removes its META some days after its last
    message (repository.py), and the assistant table's stream brings that
    removal here (template.yaml filters the stream down to these): its
    messages and each agent's thread go.

One SQS message per Lambda run (BatchSize 1 in template.yaml); a failed or
timed-out run is retried, and picks up where the last one stopped, since what
it deleted is gone. So is a failed stream record. After several failures
either goes to the dead-letter queue, and an alarm fires.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response
from aws_lambda_powertools.utilities.data_classes.dynamo_db_stream_event import DynamoDBRecord, DynamoDBRecordEventName

from assistant.checkpoints import CheckpointPurge
from assistant.container import get_checkpoint_purge, get_purged_conversations
from assistant.context import checkpoint_session
from assistant.repository import ConversationRepository, conversation_of

logger = Logger()

# Who removed an item: DynamoDB's TTL, not a user's delete (which has no userIdentity).
TTL = {"type": "Service", "principalId": "dynamodb.amazonaws.com"}


def process_batch(event: dict, context, checkpoints: CheckpointPurge) -> dict:
    """SQS: conversations their users deleted."""
    def record_handler(record) -> None:
        threads = json.loads(record.body)["threads"]
        deleted = sum(checkpoints.delete(thread) for thread in threads)
        logger.info("Purged a conversation's checkpoints", extra={"records": deleted, "threads": len(threads)})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS), context=context,
    )


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
        processor=BatchProcessor(event_type=EventType.DynamoDBStreams), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    records = event.get("Records") or [{}]
    if records[0].get("eventSource") == "aws:dynamodb":
        return process_expired(event, context, get_checkpoint_purge(), get_purged_conversations())
    return process_batch(event, context, get_checkpoint_purge())
