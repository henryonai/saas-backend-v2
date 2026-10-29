"""
The purge worker: deletes what is left of a conversation that is gone.

Three ways a conversation goes, one Lambda for all:
  - deleted by its user: the HTTP API (DELETE /assistant/conversations/{id})
    removes the history at once and queues each agent's thread in the
    checkpoint table (SQS), hundreds of records for a short conversation
    (checkpoints.py);
  - its user left the workspace, or was removed from it: accounts publishes
    MemberRemoved, which EventBridge puts on the same queue, and all their
    conversations there go, messages and threads, what the assistant
    remembers about them, and their files; when a deleted workspace is
    purged (accounts' TenantDeleted), the same for each of its members, then
    the workspace's usage counters;
  - and one that writes rather than deletes: a user's export (accounts'
    ExportRequested), the assistant's part of it: their conversations (every
    message, versions too), memories and the list of their files, in each
    of their workspaces (shared/exports.py);
  - expired, on a stack that keeps conversations for a while
    (CONVERSATION_DAYS): DynamoDB's TTL removes its META some days after its
    last message (repository.py), and the assistant table's stream brings
    that removal here (template.yaml filters the stream down to these): its
    messages and each agent's thread go.

It also keeps the workspace's status (accounts' and admin's TenantSuspended,
TenantUnsuspended, TenantDeletionScheduled, TenantRestored), so no message
is answered while it isn't active (shared/workspace_status.py).

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

from boto3.dynamodb.conditions import Key

from shared.dynamo import delete_partition
from shared.exports import PartWriter, clean
from shared.workspace_status import STATUS_EVENTS, save

from assistant.checkpoints import CheckpointPurge
from assistant.container import (
    get_checkpoint_purge,
    get_export_writer,
    get_purged_conversations,
    get_purged_files,
    get_purged_memories,
)
from assistant.context import checkpoint_session
from assistant.models import iso
from assistant.repository import ConversationRepository, MemoryRepository, conversation_of
from assistant.service import FileService

logger = Logger()

# Who removed an item: DynamoDB's TTL, not a user's delete (which has no userIdentity).
TTL = {"type": "Service", "principalId": "dynamodb.amazonaws.com"}
MEMBER_REMOVED = "MemberRemoved"   # accounts' events, by name (modules never import each other)
TENANT_DELETED = "TenantDeleted"
EXPORT_REQUESTED = "ExportRequested"


def process_batch(event: dict, context, checkpoints: CheckpointPurge,
                  conversations: ConversationRepository | None = None,
                  memories: MemoryRepository | None = None, files: FileService | None = None,
                  exports: PartWriter | None = None) -> dict:
    """SQS: conversations their users deleted (the HTTP API's), and members removed (EventBridge's)."""
    def record_handler(record) -> None:
        body = json.loads(record.body)
        if body.get("detail-type") == MEMBER_REMOVED:
            purge_member(body["detail"], checkpoints, conversations, memories, files)
            return
        if body.get("detail-type") == TENANT_DELETED:
            purge_tenant(body["detail"], checkpoints, conversations, memories, files)
            return
        if body.get("detail-type") in STATUS_EVENTS:   # suspended, being deleted, or active again
            save(conversations.table, body["detail-type"], body["detail"])
            return
        if body.get("detail-type") == EXPORT_REQUESTED:
            if body["detail"]["kind"] == "user":
                export_user(body["detail"], conversations, memories, files, exports)
            return
        threads = body["threads"]
        deleted = sum(checkpoints.delete(thread) for thread in threads)
        logger.info("Purged a conversation's checkpoints", extra={"records": deleted, "threads": len(threads)})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS, logger=logger), context=context,   # logs each failure
    )


def purge_member(detail: dict, checkpoints: CheckpointPurge, conversations: ConversationRepository,
                 memories: MemoryRepository | None = None, files: FileService | None = None) -> None:
    """
    A member left their workspace, or was removed from it: their
    conversations there go, each as a user's delete would, checkpoints
    first, then what the assistant remembers about them, and their files.
    Only conversations last used before then: someone invited back since,
    whose event came late, keeps what they said since. Memories and files go
    whole.
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
    objects = files.delete_all(tenant_id, user_id) if files else 0
    logger.info("Purged a removed member's conversations", extra={"tenant_id": tenant_id, "conversations": count,
                                                                  "records": deleted, "memories": remembered,
                                                                  "file_objects": objects})


def purge_tenant(detail: dict, checkpoints: CheckpointPurge, conversations: ConversationRepository,
                 memories: MemoryRepository | None = None, files: FileService | None = None) -> None:
    """
    A deleted workspace is purged: each member's conversations, memories and
    files go, as when they leave, then the workspace's usage counters. A run
    that times out is retried and continues: what it deleted is gone.
    """
    tenant_id = detail["tenant_id"]
    for user_id in detail["user_ids"]:
        purge_member({**detail, "user_id": user_id}, checkpoints, conversations, memories, files)
    counters = delete_partition(conversations.table, f"TENANT#{tenant_id}")
    logger.info("Purged a deleted workspace", extra={"tenant_id": tenant_id, "members": len(detail["user_ids"]),
                                                     "counters": counters})


def export_user(detail: dict, conversations: ConversationRepository, memories: MemoryRepository,
                files: FileService, exports: PartWriter) -> None:
    """The assistant's part of a user's export, by workspace. File contents stay on the Files page."""
    user_id, data = detail["user_id"], {}
    for tenant_id in detail["tenant_ids"]:
        threads = []
        for conversation in conversations.last_used_by(tenant_id, user_id, "9999"):   # all of them
            pk = f"TENANT#{tenant_id}#USER#{user_id}#CONV#{conversation.thread_id}"
            items, start = [], {}
            while True:
                result = conversations.table.query(KeyConditionExpression=Key("pk").eq(pk), **start)
                items += [{"sk": item["sk"], **clean(item)} for item in result["Items"]]
                if "LastEvaluatedKey" not in result:
                    break
                start = {"ExclusiveStartKey": result["LastEvaluatedKey"]}
            threads.append({"thread_id": conversation.thread_id, "records": items})
        enabled, remembered = memories.read(tenant_id, user_id)
        listed, cursor = [], None
        while True:
            page, cursor = files.files.list(tenant_id, user_id, 100, cursor)
            listed += [f.model_dump(mode="json") for f in page]
            if not cursor:
                break
        data[tenant_id] = {"conversations": threads, "memory_enabled": enabled,
                           "memories": [m.model_dump(mode="json") for m in remembered], "files": listed}
    exports.write(detail, "assistant", data)
    logger.info("Wrote the assistant's part of an export", extra={"job_id": detail["job_id"],
                                                                   "workspaces": len(data)})


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
    return process_batch(event, context, get_checkpoint_purge(), get_purged_conversations(), get_purged_memories(),
                         get_purged_files(), get_export_writer())
