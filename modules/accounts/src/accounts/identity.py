"""
Login system (Cognito), behind a small interface so tests can use a fake.
"""
from typing import Protocol


class IdentityProvider(Protocol):
    def create_user(self, email: str) -> str:
        """Creates a login and returns the new user's id."""
        ...


class CognitoIdentity:
    def __init__(self, client, user_pool_id: str | None):
        self.client = client
        self.user_pool_id = user_pool_id

    def create_user(self, email: str) -> str:
        if not self.user_pool_id:
            raise RuntimeError("USER_POOL_ID is not configured for this function")
        # Cognito emails the user a temporary password.
        response = self.client.admin_create_user(
            UserPoolId=self.user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        attributes = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
        return attributes["sub"]
