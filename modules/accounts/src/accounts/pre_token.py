"""
Cognito "pre token generation" trigger: runs at every login and token refresh.
Adds tenant_id, role and plan to the ID token. Other modules read these from
the token, so they never need to call the accounts module.

The frontend must send the ID token to the API (V1 trigger customizes it).
"""
from accounts.container import get_account_service
from accounts.service import AccountService


def add_claims(event: dict, service: AccountService) -> dict:
    user_id = event["request"]["userAttributes"]["sub"]
    claims = service.token_claims_for(user_id)
    event["response"]["claimsOverrideDetails"] = {"claimsToAddOrOverride": claims}
    return event


def handler(event: dict, context) -> dict:
    return add_claims(event, get_account_service())
