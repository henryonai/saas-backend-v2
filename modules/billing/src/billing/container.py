"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3
from aws_lambda_powertools.utilities import parameters

from shared.events import EventBridgePublisher

from billing.provider import DevPaymentProvider, PaymentProvider
from billing.repository import ProcessedEventRepository, SubscriptionRepository
from billing.service import BillingService
from billing.settings import BillingSettings


@lru_cache
def get_settings() -> BillingSettings:
    return BillingSettings()


def _webhook_secret(settings: BillingSettings) -> str:
    if settings.payment_webhook_secret:
        return settings.payment_webhook_secret.get_secret_value()
    # Read once from SSM Parameter Store (decrypted) and cache for 5 minutes.
    return parameters.get_parameter(settings.payment_webhook_secret_param, decrypt=True, max_age=300)


def _provider(settings: BillingSettings) -> PaymentProvider:
    # Swap this line for your real provider when going live.
    return DevPaymentProvider(webhook_secret=_webhook_secret(settings), app_url=settings.app_url)


@lru_cache
def get_billing_service() -> BillingService:
    settings = get_settings()
    table = boto3.resource("dynamodb").Table(settings.billing_table)
    return BillingService(
        subscriptions=SubscriptionRepository(table),
        processed=ProcessedEventRepository(table),
        provider=_provider(settings),
        publisher=EventBridgePublisher(boto3.client("events"), settings.event_bus_name),
    )
