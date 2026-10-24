"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3
from aws_lambda_powertools.utilities import parameters
from pydantic import SecretStr

from billing.provider import DevPaymentProvider, PaymentProvider
from billing.repository import SubscriptionRepository
from billing.service import BillingService
from billing.settings import BillingSettings
from billing.stripe_provider import StripeConfig, StripePaymentProvider, new_client


@lru_cache
def get_settings() -> BillingSettings:
    return BillingSettings()


def _from_ssm(value: SecretStr | str | None, param: str | None, names: str, secret: bool = True) -> str:
    """A value set directly (local runs), or else read from SSM Parameter Store."""
    if value:
        return value.get_secret_value() if isinstance(value, SecretStr) else value
    if not param:
        raise RuntimeError(f"Set {names}")
    # Decrypted if it's a SecureString, and cached for 5 minutes, so a rotated
    # value is picked up without waiting for a cold start.
    return parameters.get_parameter(param, decrypt=secret, max_age=300)


def webhook_secret(settings: BillingSettings) -> str:
    return _from_ssm(settings.payment_webhook_secret, settings.payment_webhook_secret_param,
                     "PAYMENT_WEBHOOK_SECRET_PARAM (AWS) or PAYMENT_WEBHOOK_SECRET (local)")


def stripe_secret_key(settings: BillingSettings) -> str:
    return _from_ssm(settings.stripe_secret_key, settings.stripe_secret_key_param,
                     "STRIPE_SECRET_KEY_PARAM (AWS) or STRIPE_SECRET_KEY (local)")


def stripe_config(settings: BillingSettings) -> StripeConfig:
    return StripeConfig.parse(_from_ssm(settings.stripe_config, settings.stripe_config_param,
                                        "STRIPE_CONFIG_PARAM (AWS) or STRIPE_CONFIG (local)", secret=False))


@lru_cache(maxsize=1)
def stripe_client(api_key: str):
    return new_client(api_key)   # one client per key, so a rotated key gets a new one


def payment_provider(settings: BillingSettings) -> PaymentProvider:
    # Secrets are read only when a call needs them: the API function never
    # reads the webhook secret, and nothing is read at start-up.
    if settings.payment_provider == "stripe":
        return StripePaymentProvider(client=lambda: stripe_client(stripe_secret_key(settings)),
                                     config=lambda: stripe_config(settings),
                                     webhook_secret=lambda: webhook_secret(settings),
                                     app_url=settings.app_url)
    return DevPaymentProvider(webhook_secret=lambda: webhook_secret(settings), app_url=settings.app_url)


@lru_cache
def get_billing_service() -> BillingService:
    settings = get_settings()
    return BillingService(
        subscriptions=SubscriptionRepository(boto3.resource("dynamodb").Table(settings.billing_table)),
        provider=payment_provider(settings),
    )
