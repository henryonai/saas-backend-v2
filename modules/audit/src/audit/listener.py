"""
Listens to every event about a workspace (EventBridge -> SQS -> this Lambda)
and writes an audit entry for each (service.AUDITED); TenantDeleted removes
the workspace's log, and ExportRequested writes it as part of the export.

Events can arrive twice; a repeated one finds its entry and adds nothing.
Failed messages are retried and end up in the dead-letter queue.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from audit.container import get_audit_service
from audit.service import AuditService

logger = Logger()


def process_batch(event: dict, context, service: AuditService) -> dict:
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
    return process_batch(event, context, get_audit_service())
