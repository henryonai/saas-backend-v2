"""Checks the real wiring: settings from env, secrets from SSM Parameter Store."""
import json

import boto3
import pytest
import stripe

from billing import container
from billing.settings import BillingSettings
from billing.stripe_provider import StripePaymentProvider


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
    service = container.get_billing_service()   # nothing is read from SSM to start
    assert isinstance(service.provider, StripePaymentProvider)

    with pytest.raises(RuntimeError, match="PAYMENT_WEBHOOK_SECRET"):
        service.provider.webhook_secret()       # but a webhook would fail clearly


def test_secret_is_hidden_when_printed(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "super-secret")
    assert "super-secret" not in repr(BillingSettings(_env_file=None))


def test_stripe_is_wired_from_ssm(monkeypatch):
    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/saas/test/stripe-secret-key", Type="SecureString",
                      Value="sk_test_from_ssm")
    ssm.put_parameter(Name="/saas/test/stripe-config", Type="String", Value=json.dumps(
        {"prices": {"pro": "price_pro"}, "plan_for_price": {"price_pro": "pro"}, "portal_configuration": "bpc_1"}))
    monkeypatch.setenv("BILLING_TABLE", "billing-dev")
    monkeypatch.setenv("STRIPE_SECRET_KEY_PARAM", "/saas/test/stripe-secret-key")
    monkeypatch.setenv("STRIPE_CONFIG_PARAM", "/saas/test/stripe-config")

    provider = container.get_billing_service().provider   # nothing read yet: the webhook secret isn't set
    assert isinstance(provider, StripePaymentProvider)
    assert provider.config().prices == {"pro": "price_pro"}
    assert container.stripe_secret_key(container.get_settings()) == "sk_test_from_ssm"
    assert isinstance(provider.client(), stripe.StripeClient)
    with pytest.raises(RuntimeError, match="PAYMENT_WEBHOOK_SECRET"):
        provider.webhook_secret()
