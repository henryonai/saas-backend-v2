"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3
from aws_lambda_powertools.utilities import parameters

from billing.provider import DevPaymentProvider
from billing.repository import SubscriptionRepository
from billing.service import BillingService
from billing.settings import BillingSettings


@lru_cache
def get_settings() -> BillingSettings:
    return BillingSettings()


def webhook_secret(settings: BillingSettings) -> str:
    if settings.payment_webhook_secret:
        return settings.payment_webhook_secret.get_secret_value()
    if not settings.payment_webhook_secret_param:
        raise RuntimeError("Set PAYMENT_WEBHOOK_SECRET_PARAM (AWS) or PAYMENT_WEBHOOK_SECRET (local)")
    # Read from SSM Parameter Store (decrypted) and cached for 5 minutes, so a
    # rotated secret is picked up without waiting for a cold start.
    return parameters.get_parameter(settings.payment_webhook_secret_param, decrypt=True, max_age=300)


@lru_cache
def get_billing_service() -> BillingService:
    settings = get_settings()
    return BillingService(
        subscriptions=SubscriptionRepository(boto3.resource("dynamodb").Table(settings.billing_table)),
        # Swap this line for your real provider when going live. The secret is
        # read only when a webhook arrives, so the API function never needs it.
        provider=DevPaymentProvider(webhook_secret=lambda: webhook_secret(settings),
                                    app_url=settings.app_url),
    )
