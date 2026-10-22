import json
from types import SimpleNamespace

import pytest

from notifications.container import get_notification_service
from notifications.handler import app, handler
from notifications.repository import NotificationRepository
from notifications.service import NotificationService
from notifications.worker import process_batch
from testkit.aws import create_table
from testkit.http import FakeContext, call


def sqs_event(*messages: tuple[str, dict]) -> dict:
    """What the worker receives: SQS messages wrapping EventBridge events."""
    records = []
    for i, (event_type, detail) in enumerate(messages, start=1):
        body = {"detail-type": event_type, "source": "saas.app", "detail": detail}
        records.append({
            "messageId": f"m{i}", "receiptHandle": f"r{i}", "body": json.dumps(body),
            "attributes": {}, "messageAttributes": {}, "md5OfBody": "",
            "eventSource": "aws:sqs", "awsRegion": "ap-southeast-1",
            "eventSourceARN": "arn:aws:sqs:ap-southeast-1:123456789012:notifications",
        })
    return {"Records": records}


class FakeEmailSender:
    """Records emails instead of sending them with SES."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest.fixture
def ctx():
    email = FakeEmailSender()
    service = NotificationService(
        notifications=NotificationRepository(create_table("notifications-test")), email=email,
    )
    app.dependency_overrides[get_notification_service] = lambda: service
    yield SimpleNamespace(
        email=email,
        api=lambda *a, **kw: call(handler, *a, **kw),
        deliver=lambda event_type, detail: process_batch(
            sqs_event((event_type, detail)), FakeContext(), service),
        deliver_batch=lambda *messages: process_batch(
            sqs_event(*messages), FakeContext(), service),
    )
    app.dependency_overrides.clear()
