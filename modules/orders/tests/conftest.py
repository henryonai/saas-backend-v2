from types import SimpleNamespace

import pytest

from orders.container import get_order_service
from orders.handler import app, handler
from orders.repository import OrderRepository
from orders.service import OrderService
from testkit.aws import create_table
from testkit.fakes import FakePublisher
from testkit.http import call, claims


@pytest.fixture
def ctx():
    table = create_table("orders-test")
    publisher = FakePublisher()
    service = OrderService(orders=OrderRepository(table), publisher=publisher)
    app.dependency_overrides[get_order_service] = lambda: service
    yield SimpleNamespace(table=table, publisher=publisher,
                          api=lambda *a, **kw: call(handler, *a, **kw))
    app.dependency_overrides.clear()


@pytest.fixture
def alice():
    """A member of tenant t1 on the Free plan (claims as the login trigger adds them)."""
    return claims("alice", "t1", "member", plan="free")
