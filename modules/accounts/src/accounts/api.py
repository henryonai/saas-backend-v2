"""
HTTP routes. Each route declares what it needs with Depends(); Powertools
provides it. Routes stay thin: input -> service -> output.
"""
from typing import Annotated
from urllib.parse import unquote

from aws_lambda_powertools.event_handler import Depends, Response, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.auth import CurrentUser, get_current_user
from shared.idempotency import IdempotencyKey

from accounts.container import get_account_service
from accounts.models import Invitation, Member
from accounts.schemas import (
    AccountDelete,
    ExportOut,
    InviteRequest,
    RoleUpdate,
    TenantCreate,
    TenantDelete,
    TenantOut,
    WorkspaceOut,
)
from accounts.service import AccountService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[AccountService, Depends(get_account_service)]


def created(model) -> Response:
    return Response(status_code=201, content_type=content_types.APPLICATION_JSON,
                    body=model.model_dump())


# ---------- tenants ----------

# After this, the frontend must refresh the token so it lists the new workspace.
@router.post("/tenants")
def create_tenant(body: TenantCreate, user: User, service: Service,
                  idempotency_key: IdempotencyKey) -> Response[TenantOut]:
    return created(TenantOut.of(service.create_tenant(user, body, idempotency_key)))


# Every workspace the caller belongs to (no X-Tenant-Id needed).
@router.get("/tenants")
def list_my_workspaces(user: User, service: Service) -> list[WorkspaceOut]:
    return [WorkspaceOut.of(tenant, member) for tenant, member in service.my_workspaces(user)]


# The workspace this request acts in (X-Tenant-Id).
@router.get("/tenants/me")
def get_my_tenant(user: User, service: Service) -> TenantOut:
    return TenantOut.of(service.get_tenant(user))


# The owner deletes the workspace (X-Tenant-Id), typing its name to confirm. It can be
# restored until delete_after; then it is purged. The frontend must refresh the token.
@router.delete("/tenants/me")
def delete_my_tenant(body: TenantDelete, user: User, service: Service) -> TenantOut:
    return TenantOut.of(service.delete_tenant(user, body))


# Its owner restores a deleted workspace (named in the path: the token no longer lists it).
@router.post("/tenants/<tenant_id>/restore")
def restore_tenant(tenant_id: str, user: User, service: Service) -> TenantOut:
    return TenantOut.of(service.restore_tenant(user, tenant_id))


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


# ---------- the signed-in user's account ----------

# Deletes the caller's login and what is kept about them (no X-Tenant-Id needed),
# typing their email to confirm. They must delete the workspaces they own first.
@router.delete("/account")
def delete_my_account(body: AccountDelete, user: User, service: Service):
    service.delete_account(user, body)
    return Response(status_code=204)


# ---------- exports ----------

# The owner starts a copy of the workspace's data (X-Tenant-Id); the caller, of their own.
# Each module writes its part; GET shows the newest export, with download links once done.
@router.post("/exports/workspace")
def export_workspace(user: User, service: Service) -> Response[ExportOut]:
    return Response(status_code=202, content_type=content_types.APPLICATION_JSON,
                    body=ExportOut.of(service.request_export(user, "workspace")).model_dump())


@router.get("/exports/workspace")
def latest_workspace_export(user: User, service: Service) -> ExportOut | None:
    found = service.latest_export(user, "workspace")
    return ExportOut.of(*found) if found else None


@router.post("/exports/me")
def export_my_data(user: User, service: Service) -> Response[ExportOut]:
    return Response(status_code=202, content_type=content_types.APPLICATION_JSON,
                    body=ExportOut.of(service.request_export(user, "user")).model_dump())


@router.get("/exports/me")
def latest_my_export(user: User, service: Service) -> ExportOut | None:
    found = service.latest_export(user, "user")
    return ExportOut.of(*found) if found else None


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


# After this, the frontend must refresh the token so it lists the new workspace.
@router.post("/invitations/<tenant_id>/accept")
def accept_invitation(tenant_id: str, user: User, service: Service) -> TenantOut:
    return TenantOut.of(service.accept_invitation(user, tenant_id))


@router.post("/invitations/<tenant_id>/decline")
def decline_invitation(tenant_id: str, user: User, service: Service):
    service.decline_invitation(user, tenant_id)
    return Response(status_code=204)
