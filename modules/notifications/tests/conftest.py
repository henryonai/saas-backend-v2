from types import SimpleNamespace

import pytest

from notifications.container import get_notification_service, idempotency_store
from notifications.handler import app, handler
from notifications.repository import NotificationRepository
from notifications.service import NotificationService
from notifications.worker import process_batch
from testkit.aws import create_table
from testkit.http import FakeContext, call
from testkit.sqs import sqs_event

TABLE = "notifications-test"


class FakeEmailSender:
    """Records emails instead of sending them with SES. Can be told to fail."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.failures_left = 0

    def send(self, to: str, subject: str, body: str) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("SES rejected the email")
        self.sent.append((to, subject, body))


@pytest.fixture
def ctx():
    email = FakeEmailSender()
    service = NotificationService(
        notifications=NotificationRepository(create_table(TABLE)), email=email,
        app_url="https://app.test",
    )
    store = idempotency_store(TABLE)
    app.dependency_overrides[get_notification_service] = lambda: service

    def deliver_batch(*messages: tuple[str, dict]) -> dict:
        return process_batch(sqs_event(*messages), FakeContext(), service, store)

    yield SimpleNamespace(
        email=email,
        api=lambda *a, **kw: call(handler, *a, **kw),
        deliver=lambda event_type, detail: deliver_batch((event_type, detail)),
        deliver_batch=deliver_batch,
    )
    app.dependency_overrides.clear()
