from shared.auth import CurrentUser
from shared.errors import InvalidRequest, NotFound, PlanLimitReached
from shared.events import EventPublisher
from shared.plans import get_limit

from orders.models import Order, OrderCreate, OrderStatus
from orders.repository import OrderRepository

ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: set(),
    OrderStatus.CANCELLED: set(),
}


class OrderService:
    def __init__(self, orders: OrderRepository, publisher: EventPublisher):
        self.orders = orders
        self.publisher = publisher

    def create_order(self, user: CurrentUser, data: OrderCreate) -> Order:
        tenant_id = user.require_tenant()

        # Plan comes from the login token: no call to the accounts module.
        max_orders = get_limit(user.plan, "max_orders")
        if max_orders is not None and self.orders.count(tenant_id) >= max_orders:
            raise PlanLimitReached(f"Your {user.plan} plan allows {max_orders} orders. Upgrade for more.")

        order = Order(**data.model_dump(), tenant_id=tenant_id, created_by=user.user_id)
        self.orders.save(order)
        self.publisher.publish("OrderCreated", tenant_id, order_id=order.order_id,
                               user_id=user.user_id, item=order.item)
        return order

    def get_order(self, user: CurrentUser, order_id: str) -> Order:
        order = self.orders.get(user.require_tenant(), order_id)
        if not order:
            raise NotFound(f"Order {order_id} not found")
        return order

    def list_orders(self, user: CurrentUser, limit: int) -> list[Order]:
        return self.orders.list(user.require_tenant(), limit)

    def change_status(self, user: CurrentUser, order_id: str, new_status: OrderStatus) -> Order:
        order = self.get_order(user, order_id)
        if new_status not in ALLOWED_TRANSITIONS[order.status]:
            raise InvalidRequest(f"Cannot change order from '{order.status.value}' to '{new_status.value}'")
        self.orders.update_status(order.tenant_id, order_id, new_status.value)
        return order.model_copy(update={"status": new_status})
