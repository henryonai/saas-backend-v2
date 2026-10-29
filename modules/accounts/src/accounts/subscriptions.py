"""
Accounts' listener (EventBridge -> SQS -> this Lambda):
  - SubscriptionChanged from billing: keeps the tenant's local copy of its plan up to date;
  - ExportRequested (its own): writes accounts' part of the export;
  - the exports bucket's "Object Created": marks that part of its export done
    (shared/exports.py), and the export done once every part is there.

Events can arrive twice or out of order; the revision check in the
repository makes both harmless. Failed messages are retried and end up in
the dead-letter queue.
"""
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from accounts.container import get_account_service
from accounts.service import AccountService

logger = Logger()


def process_batch(event: dict, context, service: AccountService) -> dict:
    def record_handler(record) -> None:
        message = json.loads(record.body)
        d = message["detail"]
        if message["detail-type"] == "ExportRequested":
            service.write_export_part(d)
        elif message["detail-type"] == "Object Created":      # the exports bucket, via EventBridge
            service.export_part_written(d["object"]["key"])
        else:
            logger.info("Subscription changed", extra={"tenant_id": d["tenant_id"], "plan": d["plan"],
                                                       "revision": d["revision"]})
            service.apply_subscription_change(d["tenant_id"], d["plan"], d["status"], d["revision"])

    return process_partial_response(
        event=event, record_handler=record_handler,
        processor=BatchProcessor(event_type=EventType.SQS, logger=logger), context=context,   # logs each failure
    )


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_batch(event, context, get_account_service())
