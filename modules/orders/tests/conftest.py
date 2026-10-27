from types import SimpleNamespace
from uuid import uuid4

import pytest

from orders.container import get_order_service
from orders.handler import app, handler
from orders.repository import OrderRepository
from orders.service import OrderService
from testkit.aws import create_table
from testkit.http import call, claims
from testkit.outbox import outbox_events


def api(method: str, path: str, *args, headers: dict | None = None, **kwargs):
    """
    testkit's call. A POST /orders gets a new Idempotency-Key, as the web app
    sends one per order, unless the test passes its headers (`{}`: none).
    """
    if headers is None and (method, path) == ("POST", "/orders"):
        headers = {"Idempotency-Key": str(uuid4())}
    return call(handler, method, path, *args, headers=headers, **kwargs)


@pytest.fixture
def ctx():
    table = create_table("orders-test")
    service = OrderService(orders=OrderRepository(table))
    app.dependency_overrides[get_order_service] = lambda: service
    yield SimpleNamespace(table=table, service=service,
                          events=lambda: outbox_events(table),
                          api=api)
    app.dependency_overrides.clear()


@pytest.fixture
def alice():
    """A member of tenant t1 on the Free plan (claims as the login trigger adds them)."""
    return claims("alice", "t1", "member", plan="free")
