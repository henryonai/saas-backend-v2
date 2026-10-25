"""
The outbox: each module saves its events as items in its own table, in the
same transaction as the change, and this relay publishes them to EventBridge.

  pk = OUTBOX#<event_id>   sk = OUTBOX   (removed by DynamoDB TTL after 7 days)

The relay is one Lambda function reading each module table's DynamoDB stream
(OutboxRelayFunction in template.yaml). A failed publish is retried from the
stream until it succeeds. If it still fails when the stream record expires
(24 hours), the item is still in the table: scripts/replay-outbox.py
publishes it again. Delivery is at least once and not in order, so listeners
skip duplicates by event_id and use a version number where order matters.
"""
import json
import time
from functools import lru_cache

import boto3
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response

from shared.events import Event, EventBridgePublisher, EventPublisher
from shared.settings import BaseAppSettings

OUTBOX_TTL_SECONDS = 7 * 24 * 60 * 60  # long enough to replay after an outage

logger = Logger()
metrics = Metrics()


def outbox_put(event: Event) -> dict:
    """A transaction operation (see dynamo.transact) that saves the event."""
    return {"Put": {"Item": {
        "pk": f"OUTBOX#{event.event_id}",
        "sk": "OUTBOX",
        "event_type": event.type,
        "detail": json.dumps(event.detail()),
        "expires_at": int(time.time()) + OUTBOX_TTL_SECONDS,
    }}}


def relay(event: dict, context, publisher: EventPublisher) -> dict:
    """Publishes new outbox items from a DynamoDB stream batch."""

    def record_handler(record) -> None:
        item = record.dynamodb.new_image
        if not str(item.get("pk", "")).startswith("OUTBOX#"):
            return  # the event source filter normally drops these already
        publisher.publish(item["event_type"], json.loads(item["detail"]))
        metrics.add_metric(name="EventsPublished", unit=MetricUnit.Count, value=1)

    return process_partial_response(
        event=event, record_handler=record_handler,
        # With the logger, each failed record is a WARNING with its traceback (else only DEBUG).
        processor=BatchProcessor(event_type=EventType.DynamoDBStreams, logger=logger), context=context,
    )


class RelaySettings(BaseAppSettings):
    event_bus_name: str  # required: never fall back to the account's shared default bus


@lru_cache
def _publisher() -> EventBridgePublisher:
    return EventBridgePublisher(boto3.client("events"), RelaySettings().event_bus_name)


@logger.inject_lambda_context
@metrics.log_metrics
def handler(event: dict, context) -> dict:
    return relay(event, context, _publisher())
