from typing import Annotated

from aws_lambda_powertools.event_handler import Depends, Response
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query

from shared.auth import CurrentUser, get_current_user

from notifications.container import get_notification_service
from notifications.models import Notification
from notifications.service import NotificationService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[NotificationService, Depends(get_notification_service)]


# The frontend can poll this every 30-60 seconds.
@router.get("/notifications")
def list_notifications(user: User, service: Service,
                       limit: Annotated[int, Query(gt=0, le=50)] = 20) -> list[Notification]:
    return service.list_mine(user, limit)


@router.post("/notifications/<notification_id>/read")
def mark_read(notification_id: str, user: User, service: Service):
    service.mark_read(user, notification_id)
    return Response(status_code=204)
