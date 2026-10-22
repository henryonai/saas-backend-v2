"""
Login system (Cognito), behind a small interface so tests can use a fake.

Invitations don't create logins: invited people sign up (or sign in) with the
invited email themselves, so Cognito verifies they own it.
"""
from contextlib import suppress
from typing import Protocol


class IdentityProvider(Protocol):
    def sign_out(self, user_id: str) -> None:
        """Revokes the user's refresh tokens, so their next token has fresh claims."""
        ...


class CognitoIdentity:
    def __init__(self, client, user_pool_id: str | None):
        self.client = client
        self.user_pool_id = user_pool_id

    def sign_out(self, user_id: str) -> None:
        if not self.user_pool_id:
            raise RuntimeError("USER_POOL_ID is not configured for this function")
        # Admin APIs accept the user's sub as the username.
        with suppress(self.client.exceptions.UserNotFoundException):
            self.client.admin_user_global_sign_out(UserPoolId=self.user_pool_id, Username=user_id)
