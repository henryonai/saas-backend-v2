"""
The ONE place that decides which real classes are used ("composition root").

Built once per Lambda instance (lru_cache) and reused by warm requests.
Routes get the service through Depends(get_account_service); triggers and
listeners call get_account_service() directly. Tests replace it with
app.dependency_overrides or build AccountService with fakes.
"""
from functools import lru_cache

import boto3

from accounts.identity import CognitoIdentity
from accounts.repository import AccountRepository
from accounts.service import AccountService, InvitationLimits
from accounts.settings import AccountsSettings


@lru_cache
def get_settings() -> AccountsSettings:
    return AccountsSettings()


@lru_cache
def get_account_service() -> AccountService:
    settings = get_settings()
    return AccountService(
        accounts=AccountRepository(boto3.resource("dynamodb").Table(settings.accounts_table)),
        identity=CognitoIdentity(boto3.client("cognito-idp"), settings.user_pool_id),
        invitation_limits=InvitationLimits(
            days=settings.invitation_days,
            per_day=settings.invites_per_day,
            pending_per_email=settings.max_pending_invitations_per_email,
        ),
        max_workspaces=settings.max_workspaces_per_user,
    )
