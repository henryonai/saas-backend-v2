"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3

from admin.repository import AdminRepository
from admin.service import AdminService
from admin.settings import AdminSettings


@lru_cache
def get_settings() -> AdminSettings:
    return AdminSettings()


@lru_cache
def get_admin_service() -> AdminService:
    return AdminService(admin=AdminRepository(boto3.resource("dynamodb").Table(get_settings().admin_table)))
