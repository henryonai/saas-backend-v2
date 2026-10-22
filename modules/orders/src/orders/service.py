from shared.auth import CurrentUser
from shared.dynamo import ConditionFailed
from shared.errors import Conflict, InvalidRequest, NotFound, PlanLimitReached
from shared.events import Event
from shared.plans import get_limit

from orders.models import Order, OrderCreate, OrderPage, OrderStatus
from orders.repository import OrderRepository

ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: set(),
    OrderStatus.CANCELLED: set(),
}


class OrderService:
    def __init__(self, orders: OrderRepository):
        self.orders = orders

    def create_order(self, user: CurrentUser, data: OrderCreate) -> Order:
        tenant_id = user.require_tenant()
        order = Order(**data.model_dump(), tenant_id=tenant_id, created_by=user.user_id)
        event = Event("OrderCreated", tenant_id, {"order_id": order.order_id,
                                                   "user_id": user.user_id, "item": order.item})

        # Plan comes from the login token: no call to the accounts module.
        # The limit is checked inside the same transaction that saves the order.
        max_orders = get_limit(user.plan, "max_orders")
        try:
            self.orders.create(order, event, max_orders)
        except ConditionFailed as e:
            if "limit" not in e.failed:
                raise
            raise PlanLimitReached(f"Your {user.plan} plan allows {max_orders} orders. Upgrade for more.")
        return order

    def get_order(self, user: CurrentUser, order_id: str) -> Order:
        order = self.orders.get(user.require_tenant(), order_id)
        if not order:
            raise NotFound(f"Order {order_id} not found")
        return order

    def list_orders(self, user: CurrentUser, limit: int, cursor: str | None = None) -> OrderPage:
        orders, next_cursor = self.orders.list(user.require_tenant(), limit, cursor)
        return OrderPage(items=orders, next_cursor=next_cursor)

    def change_status(self, user: CurrentUser, order_id: str, new_status: OrderStatus) -> Order:
        order = self.get_order(user, order_id)
        if new_status not in ALLOWED_TRANSITIONS[order.status]:
            raise InvalidRequest(f"Cannot change order from '{order.status.value}' to '{new_status.value}'")
        if not self.orders.update_status(order.tenant_id, order_id, new_status, expected=order.status):
            raise Conflict("The order was changed by someone else. Reload it and try again.")
        return order.model_copy(update={"status": new_status})
