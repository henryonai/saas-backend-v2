"""
Who is calling, and in which workspace? Read from the login token, never
from the request body.

API Gateway verifies the token before our code runs. The accounts module's
login trigger adds one claim to the ID token: every workspace the user
belongs to, with their role there and the workspace's plan, as a JSON string:

  workspaces = '{"<tenant_id>": {"role": "owner", "plan": "pro"}, ...}'

A user can work in several workspaces at once (one per browser tab), so each
request names the one it acts in: the X-Tenant-Id header (the assistant on
AgentCore: forwardedProps.tenantId). Only a workspace the token lists is
accepted; the request picks, the token decides.

Claims are a snapshot from when the token was issued (ID tokens last 15
minutes, see template.yaml). The accounts module re-checks roles in its own
table for actions that change who has access.

Use in a route with dependency injection:

    @router.get("/orders")
    def list_orders(user: Annotated[CurrentUser, Depends(get_current_user)]): ...
"""
import json
from dataclasses import dataclass
from typing import Literal, get_args

from aws_lambda_powertools.event_handler.request import Request

from shared.errors import Forbidden, Unauthorized

Role = Literal["owner", "admin", "member"]
ROLE_OWNER: Role = "owner"
ROLE_ADMIN: Role = "admin"
ROLE_MEMBER: Role = "member"

WORKSPACES_CLAIM = "workspaces"
TENANT_HEADER = "x-tenant-id"   # API Gateway passes header names in lowercase


@dataclass(frozen=True)
class Workspace:
    """The caller's role in one workspace, and its plan, as the token lists them."""
    role: Role
    plan: str


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    # The workspace this request acts in, with the caller's role and its plan:
    # all three are set, or all are None (no X-Tenant-Id, or no workspace yet).
    tenant_id: str | None
    role: Role | None
    plan: str | None
    email_verified: bool = False

    def require_tenant(self) -> str:
        if not self.tenant_id:
            raise Forbidden("Choose one of your workspaces (the X-Tenant-Id header), or create or join one first")
        return self.tenant_id

    def require_role(self, *allowed: str) -> str:
        """Checks the role and returns the tenant id."""
        tenant_id = self.require_tenant()
        if self.role not in allowed:
            raise Forbidden(f"This action needs one of these roles: {', '.join(allowed)}")
        return tenant_id


def get_current_user(request: Request) -> CurrentUser:
    """Dependency: builds CurrentUser from the verified JWT claims and the requested workspace."""
    claims = request.resolved_event.request_context.authorizer.jwt_claim or {}
    return user_from_claims(claims, (request.headers or {}).get(TENANT_HEADER))


def user_from_claims(claims: dict, tenant_id: str | None = None) -> CurrentUser:
    """
    Builds CurrentUser from claims that something already verified (API
    Gateway's JWT authorizer, or AgentCore Runtime's for the assistant),
    acting in `tenant_id` if given.
    """
    # The authorizer also accepts access tokens, which carry no email or
    # workspace claims. Only ID tokens are customized by the login trigger.
    if claims.get("token_use") != "id":
        raise Unauthorized("Send the ID token in the Authorization header")

    workspaces = workspaces_in(claims)   # read even when unused: a malformed claim is always refused
    workspace = workspaces.get(tenant_id) if tenant_id else None
    if tenant_id and not workspace:
        # Usually a token from before the user joined it: refresh the token and retry.
        raise Unauthorized("Your token doesn't list this workspace; refresh it and try again")

    return CurrentUser(
        user_id=claims["sub"],
        email=claims.get("email", ""),
        tenant_id=tenant_id if workspace else None,
        role=workspace.role if workspace else None,
        plan=workspace.plan if workspace else None,
        # API Gateway passes claim values as strings.
        email_verified=str(claims.get("email_verified", "")).lower() == "true",
    )


def workspaces_in(claims: dict) -> dict[str, Workspace]:
    """The workspaces the token lists, by tenant id. Empty right after sign-up."""
    raw = claims.get(WORKSPACES_CLAIM)
    if not raw:
        return {}
    try:
        listed = json.loads(raw)
        workspaces = {tenant_id: Workspace(role=w["role"], plan=w["plan"]) for tenant_id, w in listed.items()}
    except (TypeError, ValueError, KeyError, AttributeError):
        raise Unauthorized("Token has an unreadable workspaces claim; sign in again") from None
    # The login trigger writes only known roles and a plan; anything else is not a token we issued.
    for w in workspaces.values():
        if w.role not in get_args(Role) or not w.plan:
            raise Unauthorized(f"Token has an unknown role {w.role!r}; sign in again")
    return workspaces
