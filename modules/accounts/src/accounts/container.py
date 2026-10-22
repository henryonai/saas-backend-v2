"""
The ONE place that decides which real classes are used ("composition root").

Built once per Lambda instance (lru_cache) and reused by warm requests.
Routes get the service through Depends(get_account_service); triggers and
listeners call get_account_service() directly. Tests replace it with
app.dependency_overrides or build AccountService with fakes.
"""
from functools import lru_cache

import boto3

from shared.events import EventBridgePublisher

from accounts.identity import CognitoIdentity
from accounts.repository import MemberRepository, TenantRepository
from accounts.service import AccountService
from accounts.settings import AccountsSettings


@lru_cache
def get_settings() -> AccountsSettings:
    return AccountsSettings()


@lru_cache
def get_account_service() -> AccountService:
    settings = get_settings()
    table = boto3.resource("dynamodb").Table(settings.accounts_table)
    return AccountService(
        tenants=TenantRepository(table),
        members=MemberRepository(table),
        identity=CognitoIdentity(boto3.client("cognito-idp"), settings.user_pool_id),
        publisher=EventBridgePublisher(boto3.client("events"), settings.event_bus_name),
    )
