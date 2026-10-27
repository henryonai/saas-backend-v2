"""
Keeps the back-office's summary of every workspace up to date (EventBridge ->
SQS -> this Lambda): its name, plan, status, members and orders, from the
events of the modules that own them. Each event is applied once (a marker in
the same transaction); a late plan revision is skipped.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from admin.container import get_admin_service
from admin.service import AdminService

logger = Logger()


def process_batch(event: dict, context, service: AdminService) -> dict:
    def record_handler(record) -> None:
        message = json.loads(record.body)
        service.handle_event(message["detail-type"], message["detail"])

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS, logger=logger), context=context,   # logs each failure
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_admin_service())
