"""Checks the real wiring: invitation rules come from settings (environment variables)."""
import pytest
from pydantic import ValidationError

from accounts import container


@pytest.fixture(autouse=True)
def fresh_container():
    container.get_settings.cache_clear()
    container.get_account_service.cache_clear()
    yield
    container.get_settings.cache_clear()
    container.get_account_service.cache_clear()


def test_invitation_limits_have_defaults(monkeypatch):
    monkeypatch.setenv("ACCOUNTS_TABLE", "accounts-dev")
    limits = container.get_account_service().limits
    assert (limits.days, limits.per_day, limits.pending_per_email) == (7, 20, 5)


def test_invitation_limits_come_from_settings(monkeypatch):
    monkeypatch.setenv("ACCOUNTS_TABLE", "accounts-dev")
    monkeypatch.setenv("INVITATION_DAYS", "3")
    monkeypatch.setenv("INVITES_PER_DAY", "50")
    monkeypatch.setenv("MAX_PENDING_INVITATIONS_PER_EMAIL", "2")
    limits = container.get_account_service().limits
    assert (limits.days, limits.per_day, limits.pending_per_email) == (3, 50, 2)


def test_workspaces_per_user_default_to_20_and_stay_within_one_login_batch(monkeypatch):
    monkeypatch.setenv("ACCOUNTS_TABLE", "accounts-dev")
    assert container.get_account_service().max_workspaces == 20
    container.get_settings.cache_clear()
    monkeypatch.setenv("MAX_WORKSPACES_PER_USER", "51")   # the login trigger reads 2 items each, 100 per batch
    with pytest.raises(ValidationError, match="max_workspaces_per_user"):
        container.get_settings()
