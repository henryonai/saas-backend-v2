"""Checks the real wiring: settings from env, secret from SSM Parameter Store."""
import boto3
import pytest

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
    assert service.provider.webhook_secret() == "from-ssm"


def test_api_function_starts_without_any_secret(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET_PARAM", raising=False)
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET", raising=False)
    service = container.get_billing_service()   # no SSM access needed to start checkout
    assert "plan=pro" in service.provider.create_checkout("t1", "pro", "a@x.com")

    with pytest.raises(RuntimeError, match="PAYMENT_WEBHOOK_SECRET"):
        service.provider.webhook_secret()       # but a webhook would fail clearly


def test_secret_is_hidden_when_printed(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "super-secret")
    assert "super-secret" not in repr(BillingSettings(_env_file=None))
