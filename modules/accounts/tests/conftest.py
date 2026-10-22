"""
Builds a real AccountService on a fake table with a fake Cognito, and injects
it into the HTTP app with dependency_overrides. No monkeypatching.
"""
from types import SimpleNamespace

import pytest

from accounts.container import get_account_service
from accounts.handler import app, handler
from accounts.pre_token import add_claims
from accounts.repository import AccountRepository
from accounts.service import AccountService, InvitationLimits
from testkit.aws import create_table
from testkit.http import call, claims
from testkit.outbox import outbox_events

LIMITS = InvitationLimits(days=7, per_day=5, pending_per_email=5)


class FakeIdentity:
    """Stands in for Cognito: records who was signed out."""

    def __init__(self):
        self.signed_out: list[str] = []

    def sign_out(self, user_id: str) -> None:
        self.signed_out.append(user_id)


@pytest.fixture
def ctx():
    table = create_table("accounts-test", with_gsi1=True)
    identity = FakeIdentity()
    service = AccountService(accounts=AccountRepository(table), identity=identity, invitation_limits=LIMITS)
    app.dependency_overrides[get_account_service] = lambda: service

    def login(user_id: str) -> dict:
        """What the login trigger adds to the token for this user."""
        event = add_claims({"request": {"userAttributes": {"sub": user_id}}, "response": {}}, service)
        return event["response"]["claimsOverrideDetails"]["claimsToAddOrOverride"]

    yield SimpleNamespace(table=table, service=service, identity=identity, login=login,
                          events=lambda: outbox_events(table),
                          api=lambda *a, **kw: call(handler, *a, **kw))
    app.dependency_overrides.clear()


@pytest.fixture
def owner(ctx):
    """'alice' signs up and creates a workspace. Returns her claims after token refresh."""
    status, tenant = ctx.api("POST", "/tenants", claims("alice"), {"name": "Acme"})
    assert status == 201
    return claims("alice", tenant["tenant_id"], "owner")


@pytest.fixture
def join(ctx, owner):
    """Invites `name`, who signs in and accepts; returns their refreshed token claims."""

    def _join(name: str, role: str = "member") -> dict:
        status, _ = ctx.api("POST", "/users/invite", owner, {"email": f"{name}@example.com", "role": role})
        assert status == 201
        status, _ = ctx.api("POST", f"/invitations/{owner['tenant_id']}/accept", claims(name))
        assert status == 200
        token = ctx.login(name)
        assert token["tenant_id"] == owner["tenant_id"] and token["role"] == role
        return claims(name, token["tenant_id"], token["role"])

    return _join
