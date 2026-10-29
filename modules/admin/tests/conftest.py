from types import SimpleNamespace

import pytest

from admin.container import get_admin_service
from admin.handler import app, handler
from admin.listener import process_batch
from admin.repository import AdminRepository
from admin.service import AdminService
from testkit.aws import create_table
from testkit.http import FakeContext, call, claims
from testkit.outbox import outbox_events
from testkit.sqs import sqs_event


@pytest.fixture
def ctx():
    table = create_table("admin-test")
    service = AdminService(admin=AdminRepository(table))
    app.dependency_overrides[get_admin_service] = lambda: service
    yield SimpleNamespace(
        table=table, service=service, events=lambda: outbox_events(table),
        api=lambda *a, **kw: call(handler, *a, **kw),
        deliver=lambda *messages: process_batch(sqs_event(*messages), FakeContext(), service),
    )
    app.dependency_overrides.clear()


@pytest.fixture
def ops():
    """A platform admin: in the platform-admins group, in no workspace."""
    return {**claims("ops"), "cognito:groups": "[platform-admins]"}
