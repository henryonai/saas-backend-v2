"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3

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


@lru_cache
def get_notification_service() -> NotificationService:
    settings = get_settings()
    return NotificationService(
        notifications=NotificationRepository(
            boto3.resource("dynamodb").Table(settings.notifications_table)
        ),
        email=_email_sender(settings),
    )
