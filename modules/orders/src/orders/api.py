from typing import Annotated

from aws_lambda_powertools.event_handler import Depends, Response, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.openapi.params import Query

from shared.auth import CurrentUser, get_current_user

from orders.container import get_order_service
from orders.models import Order, OrderCreate, OrderPage, OrderUpdateStatus
from orders.service import OrderService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[OrderService, Depends(get_order_service)]


@router.post("/orders")
def create_order(body: OrderCreate, user: User, service: Service) -> Response[Order]:
    order = service.create_order(user, body)
    return Response(status_code=201, content_type=content_types.APPLICATION_JSON,
                    body=order.model_dump(mode="json"))


@router.get("/orders")
def list_orders(user: User, service: Service,
                limit: Annotated[int, Query(gt=0, le=100)] = 20,
                cursor: Annotated[str | None, Query(max_length=100)] = None) -> OrderPage:
    """Newest first. Pass next_cursor from the previous page as ?cursor= to continue."""
    return service.list_orders(user, limit, cursor)


@router.get("/orders/<order_id>")
def get_order(order_id: str, user: User, service: Service) -> Order:
    return service.get_order(user, order_id)


@router.patch("/orders/<order_id>/status")
def update_status(order_id: str, body: OrderUpdateStatus, user: User, service: Service) -> Order:
    return service.change_status(user, order_id, body.status)
