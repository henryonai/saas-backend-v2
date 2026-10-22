"""Checks the real wiring: settings from env, secret from SSM Parameter Store."""
import boto3
import pytest
from pydantic import ValidationError

from billing import container
from billing.settings import BillingSettings


@pytest.fixture(autouse=True)
def fresh_container():
    container.get_settings.cache_clear()
    container.get_billing_service.cache_clear()
    yield
    container.get_settings.cache_clear()
    container.get_billing_service.cache_clear()


def test_secret_is_read_from_ssm(monkeypatch):
    boto3.client("ssm").put_parameter(Name="/saas/test/webhook-secret",
                                      Value="from-ssm", Type="SecureString")
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET_PARAM", "/saas/test/webhook-secret")

    service = container.get_billing_service()
    assert service.provider.webhook_secret == "from-ssm"


def test_secret_source_is_required(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET_PARAM", raising=False)
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValidationError, match="PAYMENT_WEBHOOK_SECRET"):
        BillingSettings(_env_file=None)


def test_secret_is_hidden_when_printed(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "super-secret")
    assert "super-secret" not in repr(BillingSettings(_env_file=None))
