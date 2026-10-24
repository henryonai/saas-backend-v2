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
from typing import Literal, get_args

from aws_lambda_powertools.event_handler.request import Request

from shared.errors import Forbidden, Unauthorized

Role = Literal["owner", "admin", "member"]
ROLE_OWNER: Role = "owner"
ROLE_ADMIN: Role = "admin"
ROLE_MEMBER: Role = "member"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    # The workspace claims come together: all three are set, or all are None
    # (right after sign-up, before creating or joining a workspace, or after
    # leaving one). get_current_user enforces this.
    tenant_id: str | None
    role: Role | None
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
    return user_from_claims(request.resolved_event.request_context.authorizer.jwt_claim or {})


def user_from_claims(claims: dict) -> CurrentUser:
    """
    Builds CurrentUser from claims that something already verified (API
    Gateway's JWT authorizer, or AgentCore Runtime's for the assistant).
    """
    # The authorizer also accepts access tokens, which carry no email or
    # tenant claims. Only ID tokens are customized by the login trigger.
    if claims.get("token_use") != "id":
        raise Unauthorized("Send the ID token in the Authorization header")

    workspace = [claims.get(name) or None for name in ("tenant_id", "role", "plan")]
    # The login trigger always sets all three or none; anything else is not a token we issued.
    if any(workspace) and not all(workspace):
        raise Unauthorized("Token has incomplete workspace claims; sign in again")
    tenant_id, role, plan = workspace
    if role is not None and role not in get_args(Role):
        raise Unauthorized(f"Token has an unknown role {role!r}; sign in again")

    return CurrentUser(
        user_id=claims["sub"],
        email=claims.get("email", ""),
        tenant_id=tenant_id,
        role=role,
        plan=plan,
        # API Gateway passes claim values as strings.
        email_verified=str(claims.get("email_verified", "")).lower() == "true",
    )
