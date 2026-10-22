"""
SQS worker: EventBridge rule -> SQS queue -> this Lambda.

Failed messages are retried individually (partial batch response) and end up
in the dead-letter queue after several failures. Each event is processed once
(Powertools idempotency, keyed by event_id): a repeated delivery is skipped,
and a failed attempt is cleared so the retry runs again.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response
from aws_lambda_powertools.utilities.idempotency import IdempotencyConfig, idempotent_function
from aws_lambda_powertools.utilities.idempotency.persistence.base import BasePersistenceLayer

from notifications.container import get_idempotency_store, get_notification_service
from notifications.service import NotificationService

logger = Logger()

# Longer than SQS keeps a message (4 days by default), so any redelivery is caught.
IDEMPOTENCY = IdempotencyConfig(event_key_jmespath="detail.event_id", expires_after_seconds=7 * 24 * 3600)


def process_batch(event: dict, context, service: NotificationService, store: BasePersistenceLayer) -> dict:
    IDEMPOTENCY.register_lambda_context(context)  # a timed-out attempt can be retried

    @idempotent_function(data_keyword_argument="message", config=IDEMPOTENCY, persistence_store=store)
    def handle_message(message: dict) -> None:
        logger.info("Handling event", extra={"type": message["detail-type"]})
        service.handle_event(message["detail-type"], message["detail"])

    def record_handler(record) -> None:
        handle_message(message=json.loads(record.body))

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_notification_service(), get_idempotency_store())
