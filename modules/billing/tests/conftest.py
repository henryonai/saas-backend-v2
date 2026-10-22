import itertools
import json
from types import SimpleNamespace

import pytest

from billing.container import get_billing_service
from billing.handler import app, handler
from billing.provider import DevPaymentProvider
from billing.repository import SubscriptionRepository
from billing.service import BillingService
from billing.webhook import process_webhook
from testkit.aws import create_table
from testkit.http import call, claims
from testkit.outbox import outbox_events


def build_service(table) -> BillingService:
    return BillingService(
        subscriptions=SubscriptionRepository(table),
        provider=DevPaymentProvider(webhook_secret=lambda: "test-secret", app_url="http://app.test"),
    )


@pytest.fixture
def ctx():
    table = create_table("billing-test")
    service = build_service(table)
    app.dependency_overrides[get_billing_service] = lambda: service
    clock = itertools.count(1_700_000_000)  # provider event times, increasing

    def webhook(payload: dict, signature: str | None = None, using=None) -> int:
        payload = {"created": next(clock), **payload}
        raw = json.dumps(payload)
        sig = service.provider.sign(raw) if signature is None else signature
        event = {"headers": {"X-Signature": sig}, "body": raw, "isBase64Encoded": False}
        return process_webhook(event, using or service)["statusCode"]

    yield SimpleNamespace(table=table, service=service, webhook=webhook,
                          events=lambda: outbox_events(table),
                          event_types=lambda: [e["type"] for e in outbox_events(table)],
                          api=lambda *a, **kw: call(handler, *a, **kw))
    app.dependency_overrides.clear()


@pytest.fixture
def owner():
    return claims("alice", "t1", "owner")
