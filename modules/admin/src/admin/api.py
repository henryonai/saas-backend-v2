"""
The back-office's routes, for platform admins only (the platform-admins group
in the ID token). No X-Tenant-Id: the workspace is named in the path.
"""
from typing import Annotated

from aws_lambda_powertools.event_handler import Depends, Response
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query

from shared.auth import CurrentUser, get_current_user

from admin.container import get_admin_service
from admin.models import WorkspaceSummary
from admin.schemas import AdminLogPage, PlanGrant, Suspension, WorkspacePage
from admin.service import AdminService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[AdminService, Depends(get_admin_service)]
Limit = Annotated[int, Query(gt=0, le=100)]
Cursor = Annotated[str | None, Query(max_length=500)]


@router.get("/admin/tenants")
def list_workspaces(user: User, service: Service, limit: Limit = 50, cursor: Cursor = None) -> WorkspacePage:
    items, next_cursor = service.list_workspaces(user, limit, cursor)
    return WorkspacePage(items=items, next_cursor=next_cursor)


@router.get("/admin/tenants/<tenant_id>")
def get_workspace(tenant_id: str, user: User, service: Service) -> WorkspaceSummary:
    return service.get_workspace(user, tenant_id)


# Billing applies it, then the workspace's plan changes everywhere (202: not yet).
@router.post("/admin/tenants/<tenant_id>/plan")
def grant_plan(tenant_id: str, body: PlanGrant, user: User, service: Service):
    service.grant_plan(user, tenant_id, body.plan, body.note)
    return Response(status_code=202)


@router.post("/admin/tenants/<tenant_id>/suspend")
def suspend(tenant_id: str, body: Suspension, user: User, service: Service):
    service.suspend(user, tenant_id, body.reason)
    return Response(status_code=202)


@router.post("/admin/tenants/<tenant_id>/unsuspend")
def unsuspend(tenant_id: str, user: User, service: Service):
    service.unsuspend(user, tenant_id)
    return Response(status_code=202)


@router.get("/admin/log")
def admin_log(user: User, service: Service, limit: Limit = 50, cursor: Cursor = None) -> AdminLogPage:
    items, next_cursor = service.log(user, limit, cursor)
    return AdminLogPage(items=items, next_cursor=next_cursor)
