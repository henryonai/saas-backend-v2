"""
Who is calling? Read from the login token, never from the request body.

API Gateway verifies the token before our code runs. The accounts module's
login trigger adds these claims to the ID token:
  tenant_id, role, plan

Claims are a snapshot from when the token was issued (ID tokens last 15
minutes, see template.yaml). The accounts module re-checks roles in its own
table for actions that change who has access.

Use in a route with dependency injection:

    @router.get("/orders")
    def list_orders(user: Annotated[CurrentUser, Depends(get_current_user)]): ...
"""
from dataclasses import dataclass

from aws_lambda_powertools.event_handler.request import Request

from shared.errors import Forbidden, Unauthorized

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    tenant_id: str | None  # None right after sign-up, before creating a workspace
    role: str | None
    plan: str | None
    email_verified: bool = False

    def require_tenant(self) -> str:
        if not self.tenant_id:
            raise Forbidden("Create or join a workspace first")
        return self.tenant_id

    def require_role(self, *allowed: str) -> str:
        """Checks the role and returns the tenant id."""
        tenant_id = self.require_tenant()
        if self.role not in allowed:
            raise Forbidden(f"This action needs one of these roles: {', '.join(allowed)}")
        return tenant_id


def get_current_user(request: Request) -> CurrentUser:
    """Dependency: builds CurrentUser from the verified JWT claims."""
    claims = request.resolved_event.request_context.authorizer.jwt_claim or {}
    # The authorizer also accepts access tokens, which carry no email or
    # tenant claims. Only ID tokens are customized by the login trigger.
    if claims.get("token_use") != "id":
        raise Unauthorized("Send the ID token in the Authorization header")
    return CurrentUser(
        user_id=claims["sub"],
        email=claims.get("email", ""),
        tenant_id=claims.get("tenant_id") or None,
        role=claims.get("role") or None,
        plan=claims.get("plan") or None,
        # API Gateway passes claim values as strings.
        email_verified=str(claims.get("email_verified", "")).lower() == "true",
    )
