"""
SQS worker: deletes a deleted conversation's copy in AgentCore Memory.

The HTTP API (DELETE /assistant/conversations/{id}) removes the history at
once and queues this: the conversation's saved exchanges and each agent's
checkpoints, which Memory deletes one event at a time (hundreds for a short
conversation). One message per Lambda run (BatchSize 1 in template.yaml); a
failed or timed-out run is retried, and picks up where the last one stopped,
since what it deleted is gone. After several failures the message goes to
the dead-letter queue, and an alarm fires.

What the agent learned about the user in general (facts, preferences) is
not per conversation, and stays.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from assistant.container import get_memory
from assistant.memory import AgentCoreMemory

logger = Logger()


def process_batch(event: dict, context, memory: AgentCoreMemory) -> dict:
    def record_handler(record) -> None:
        purge = json.loads(record.body)
        deleted = memory.forget(purge["actor_id"], purge["session_ids"])
        logger.info("Purged a conversation from memory", extra={"events": deleted,
                                                                 "sessions": len(purge["session_ids"])})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_memory())
