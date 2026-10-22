"""
SQS worker: EventBridge rule -> SQS queue -> this Lambda.

Failed messages are retried individually (partial batch response) and end up
in the dead-letter queue after several failures.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from notifications.container import get_notification_service
from notifications.service import NotificationService

logger = Logger()


def process_batch(event: dict, context, service: NotificationService) -> dict:
    def record_handler(record) -> None:
        eventbridge_event = json.loads(record.body)
        logger.info("Handling event", extra={"type": eventbridge_event["detail-type"]})
        service.handle_event(eventbridge_event["detail-type"], eventbridge_event["detail"])

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS), context=context,
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_notification_service())
