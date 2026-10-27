from typing import Annotated

from aws_lambda_powertools.event_handler import Depends
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query

from shared.auth import CurrentUser, get_current_user

from audit.container import get_audit_service
from audit.schemas import AuditPage
from audit.service import AuditService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[AuditService, Depends(get_audit_service)]


# Owners and admins: who did what in the workspace (X-Tenant-Id), newest first.
@router.get("/audit")
def list_audit(user: User, service: Service,
               limit: Annotated[int, Query(gt=0, le=100)] = 50,
               cursor: Annotated[str | None, Query(max_length=500)] = None) -> AuditPage:
    entries, next_cursor = service.list(user, limit, cursor)
    return AuditPage(items=entries, next_cursor=next_cursor)
