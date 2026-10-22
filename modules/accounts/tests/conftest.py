"""
Builds a real AccountService on a fake table with fake Cognito and events,
and injects it into the HTTP app with dependency_overrides. No monkeypatching.
"""
from types import SimpleNamespace

import pytest

from accounts.container import get_account_service
from accounts.handler import app, handler
from accounts.repository import MemberRepository, TenantRepository
from accounts.service import AccountService
from testkit.aws import create_table
from testkit.fakes import FakeIdentity, FakePublisher
from testkit.http import call, claims


@pytest.fixture
def ctx():
    table = create_table("accounts-test", with_gsi1=True)
    publisher, identity = FakePublisher(), FakeIdentity()
    service = AccountService(tenants=TenantRepository(table), members=MemberRepository(table),
                             identity=identity, publisher=publisher)
    app.dependency_overrides[get_account_service] = lambda: service
    yield SimpleNamespace(service=service, publisher=publisher, identity=identity,
                          api=lambda *a, **kw: call(handler, *a, **kw))
    app.dependency_overrides.clear()


@pytest.fixture
def owner(ctx):
    """'alice' signs up and creates a workspace. Returns her claims after token refresh."""
    status, tenant = ctx.api("POST", "/tenants", claims("alice"), {"name": "Acme"})
    assert status == 201
    return claims("alice", tenant["tenant_id"], "owner")
