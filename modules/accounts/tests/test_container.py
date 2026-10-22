"""Checks the real wiring: invitation rules come from settings (environment variables)."""
import pytest

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
