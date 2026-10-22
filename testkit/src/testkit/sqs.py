"""Builds the event an SQS worker receives: SQS messages wrapping EventBridge events."""
import json


def sqs_event(*messages: tuple[str, dict]) -> dict:
    records = []
    for i, (event_type, detail) in enumerate(messages, start=1):
        body = {"detail-type": event_type, "source": "saas.app", "detail": detail}
        records.append({
            "messageId": f"m{i}", "receiptHandle": f"r{i}", "body": json.dumps(body),
            "attributes": {}, "messageAttributes": {}, "md5OfBody": "",
            "eventSource": "aws:sqs", "awsRegion": "ap-southeast-1",
            "eventSourceARN": "arn:aws:sqs:ap-southeast-1:123456789012:queue",
        })
    return {"Records": records}


def with_event_ids(detail: dict) -> dict:
    """Adds the event_id and occurred_at every published event carries (shared/events.py)."""
    from uuid import uuid4

    from shared.events import now_iso
    return {"event_id": str(uuid4()), "occurred_at": now_iso(), **detail}
