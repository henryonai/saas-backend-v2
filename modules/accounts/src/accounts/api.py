"""
HTTP routes. Each route declares what it needs with Depends(); Powertools
provides it. Routes stay thin: input -> service -> output.
"""
from typing import Annotated
from urllib.parse import unquote

from aws_lambda_powertools.event_handler import Depends, Response, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.auth import CurrentUser, get_current_user

from accounts.container import get_account_service
from accounts.models import Invitation, InviteRequest, Member, RoleUpdate, Tenant, TenantCreate
from accounts.service import AccountService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[AccountService, Depends(get_account_service)]


def created(model) -> Response:
    return Response(status_code=201, content_type=content_types.APPLICATION_JSON,
                    body=model.model_dump())


# ---------- tenants ----------

# After this, the frontend must refresh the token so it contains tenant_id.
@router.post("/tenants")
def create_tenant(body: TenantCreate, user: User, service: Service) -> Response[Tenant]:
    return created(service.create_tenant(user, body))


@router.get("/tenants/me")
def get_my_tenant(user: User, service: Service) -> Tenant:
    return service.get_tenant(user)


# ---------- users ----------

@router.get("/users")
def list_users(user: User, service: Service) -> list[Member]:
    return service.list_members(user)


@router.patch("/users/<user_id>/role")
def change_role(user_id: str, body: RoleUpdate, user: User, service: Service) -> Member:
    return service.change_role(user, user_id, body.role)


# Removing your own user id means leaving the workspace.
@router.delete("/users/<user_id>")
def remove_user(user_id: str, user: User, service: Service):
    service.remove(user, user_id)
    return Response(status_code=204)


# ---------- invitations sent by this workspace ----------

@router.post("/users/invite")
def invite_user(body: InviteRequest, user: User, service: Service) -> Response[Invitation]:
    return created(service.invite(user, body))


@router.get("/users/invitations")
def list_invitations(user: User, service: Service) -> list[Invitation]:
    return service.list_invitations(user)


@router.delete("/users/invitations/<email>")
def cancel_invitation(email: str, user: User, service: Service):
    service.cancel_invitation(user, unquote(email))
    return Response(status_code=204)


# ---------- invitations received by the signed-in user ----------

@router.get("/invitations")
def my_invitations(user: User, service: Service) -> list[Invitation]:
    return service.my_invitations(user)


# After this, the frontend must refresh the token so it contains tenant_id.
@router.post("/invitations/<tenant_id>/accept")
def accept_invitation(tenant_id: str, user: User, service: Service) -> Tenant:
    return service.accept_invitation(user, tenant_id)


@router.post("/invitations/<tenant_id>/decline")
def decline_invitation(tenant_id: str, user: User, service: Service):
    service.decline_invitation(user, tenant_id)
    return Response(status_code=204)
