"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3

from shared.exports import S3PartWriter

from audit.repository import AuditRepository
from audit.service import AuditService
from audit.settings import AuditSettings


@lru_cache
def get_settings() -> AuditSettings:
    return AuditSettings()


@lru_cache
def get_audit_service() -> AuditService:
    settings = get_settings()
    return AuditService(entries=AuditRepository(boto3.resource("dynamodb").Table(settings.audit_table)),
                        retention_days=settings.audit_retention_days,
                        exports=S3PartWriter(boto3.client("s3"), settings.exports_bucket))
