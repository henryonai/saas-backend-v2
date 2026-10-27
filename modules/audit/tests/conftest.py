from types import SimpleNamespace

import pytest

from audit.container import get_audit_service
from audit.handler import app, handler
from audit.listener import process_batch
from audit.repository import AuditRepository
from audit.service import AuditService
from shared.exports import FakePartWriter
from testkit.aws import create_table
from testkit.http import FakeContext, call
from testkit.sqs import sqs_event


@pytest.fixture
def ctx():
    exports = FakePartWriter()
    table = create_table("audit-test")
    service = AuditService(entries=AuditRepository(table), retention_days=365, exports=exports)
    app.dependency_overrides[get_audit_service] = lambda: service
    yield SimpleNamespace(
        table=table, service=service, exports=exports,
        api=lambda *a, **kw: call(handler, *a, **kw),
        deliver=lambda *messages: process_batch(sqs_event(*messages), FakeContext(), service),
    )
    app.dependency_overrides.clear()
