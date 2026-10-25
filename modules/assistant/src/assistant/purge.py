"""
SQS worker: deletes a deleted conversation's checkpoints, the agents' own
copy of it.

The HTTP API (DELETE /assistant/conversations/{id}) removes the history at
once and queues this: each agent's thread in the checkpoint table, hundreds
of records for a short conversation (checkpoints.py). One message per Lambda
run (BatchSize 1 in template.yaml); a failed or timed-out run is retried, and
picks up where the last one stopped, since what it deleted is gone. After
several failures the message goes to the dead-letter queue, and an alarm
fires.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from assistant.checkpoints import CheckpointPurge
from assistant.container import get_checkpoint_purge

logger = Logger()


def process_batch(event: dict, context, checkpoints: CheckpointPurge) -> dict:
    def record_handler(record) -> None:
        threads = json.loads(record.body)["threads"]
        deleted = sum(checkpoints.delete(thread) for thread in threads)
        logger.info("Purged a conversation's checkpoints", extra={"records": deleted, "threads": len(threads)})

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_checkpoint_purge())
