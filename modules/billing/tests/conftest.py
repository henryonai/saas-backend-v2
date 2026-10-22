import json
from types import SimpleNamespace

import pytest

from billing.container import get_billing_service
from billing.handler import app, handler
from billing.provider import DevPaymentProvider
from billing.repository import ProcessedEventRepository, SubscriptionRepository
from billing.service import BillingService
from billing.webhook import process_webhook
from testkit.aws import create_table
from testkit.fakes import FakePublisher
from testkit.http import call, claims


def build_service(table, publisher) -> BillingService:
    return BillingService(
        subscriptions=SubscriptionRepository(table),
        processed=ProcessedEventRepository(table),
        provider=DevPaymentProvider(webhook_secret="test-secret", app_url="http://app.test"),
        publisher=publisher,
    )


@pytest.fixture
def ctx():
    table = create_table("billing-test")
    publisher = FakePublisher()
    service = build_service(table, publisher)
    app.dependency_overrides[get_billing_service] = lambda: service

    def webhook(payload: dict, signature: str | None = None, using=None) -> int:
        raw = json.dumps(payload)
        sig = service.provider.sign(raw) if signature is None else signature
        event = {"headers": {"X-Signature": sig}, "body": raw, "isBase64Encoded": False}
        return process_webhook(event, using or service)["statusCode"]

    yield SimpleNamespace(table=table, publisher=publisher, webhook=webhook,
                          build_service=lambda pub: build_service(table, pub),
                          api=lambda *a, **kw: call(handler, *a, **kw))
    app.dependency_overrides.clear()


@pytest.fixture
def owner():
    return claims("alice", "t1", "owner")
