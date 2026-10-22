"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3
from aws_lambda_powertools.utilities.idempotency import DynamoDBPersistenceLayer

from notifications.email import EmailSender, LogOnlyEmailSender, SesEmailSender
from notifications.repository import NotificationRepository
from notifications.service import NotificationService
from notifications.settings import NotificationsSettings


@lru_cache
def get_settings() -> NotificationsSettings:
    return NotificationsSettings()


def _email_sender(settings: NotificationsSettings) -> EmailSender:
    if settings.email_sender:
        return SesEmailSender(boto3.client("ses"), settings.email_sender)
    return LogOnlyEmailSender()


def idempotency_store(table_name: str, client=None) -> DynamoDBPersistenceLayer:
    """Processed-event records, kept in this module's table and removed by TTL."""
    return DynamoDBPersistenceLayer(
        table_name=table_name, key_attr="pk", sort_key_attr="sk",
        static_pk_value="IDEMPOTENCY#notifications-worker", expiry_attr="expires_at",
        boto3_client=client,
    )


@lru_cache
def get_idempotency_store() -> DynamoDBPersistenceLayer:
    return idempotency_store(get_settings().notifications_table, boto3.client("dynamodb"))


@lru_cache
def get_notification_service() -> NotificationService:
    settings = get_settings()
    return NotificationService(
        notifications=NotificationRepository(
            boto3.resource("dynamodb").Table(settings.notifications_table)
        ),
        email=_email_sender(settings),
        app_url=settings.app_url,
    )
