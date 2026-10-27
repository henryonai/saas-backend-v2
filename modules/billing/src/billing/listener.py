"""
Listens to events from other modules (EventBridge -> SQS -> this Lambda):
TenantDeleted, when a deleted workspace is purged, cancels its subscription.

Events can arrive twice; a subscription cancelled already is left as it is.
Failed messages are retried and end up in the dead-letter queue.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from billing.container import get_billing_service
from billing.service import BillingService

logger = Logger()


def process_batch(event: dict, context, service: BillingService) -> dict:
    def record_handler(record) -> None:
        message = json.loads(record.body)
        logger.info("Handling event", extra={"type": message["detail-type"],
                                             "tenant_id": message["detail"]["tenant_id"]})
        service.handle_event(message["detail-type"], message["detail"])

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS, logger=logger), context=context,   # logs each failure
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_billing_service())
