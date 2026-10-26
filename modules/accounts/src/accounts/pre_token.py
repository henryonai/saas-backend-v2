"""
Cognito "pre token generation" trigger: runs at every login and token refresh.
Adds the "workspaces" claim to the ID token: every workspace the user belongs
to, with their role there and its plan. Other modules read it from the token
(shared/auth.py), so they never need to call the accounts module.

The frontend must send the ID token to the API (V1 trigger customizes it).
"""
from aws_lambda_powertools import Logger

from accounts.container import get_account_service
from accounts.service import AccountService

logger = Logger()


def add_claims(event: dict, service: AccountService) -> dict:
    claims = service.token_claims_for(event["request"]["userAttributes"]["sub"])
    event["response"]["claimsOverrideDetails"] = {"claimsToAddOrOverride": claims}
    return event


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return add_claims(event, get_account_service())
