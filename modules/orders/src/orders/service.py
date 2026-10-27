from shared.auth import CurrentUser
from shared.dynamo import ConditionFailed
from shared.errors import Conflict, InvalidRequest, NotFound, PlanLimitReached
from shared.idempotency import IDEMPOTENCY, fingerprint
from shared.plans import get_limit

from orders import events
from orders.models import Order, OrderStatus
from orders.repository import OrderRepository
from orders.schemas import OrderCreate

ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: set(),
    OrderStatus.CANCELLED: set(),
}


class OrderService:
    def __init__(self, orders: OrderRepository):
        self.orders = orders

    def create_order(self, user: CurrentUser, data: OrderCreate, idempotency_key: str) -> Order:
        """
        A retry of the same request with the same Idempotency-Key (a timeout,
        a double click) returns the order the first one made instead of making
        another; the same key with a different request is refused.
        """
        tenant_id = user.require_tenant()
        # Copy fields explicitly: a new request field must not end up stored by accident.
        order = Order(tenant_id=tenant_id, created_by=user.user_id,
                      item=data.item, quantity=data.quantity, price_cents=data.price_cents)
        request = fingerprint(data.model_dump(mode="json"))

        # Plan comes from the login token: no call to the accounts module.
        # The limit is checked inside the same transaction that saves the order.
        max_orders = get_limit(user.plan, "max_orders")
        try:
            self.orders.create(order, events.order_created(order), max_orders, idempotency=(idempotency_key, request))
        except ConditionFailed as e:
            if IDEMPOTENCY in e.failed:   # before the limit: the order it made is counted already
                return self.get_order(user, self.orders.order_for_key(tenant_id, user.user_id, idempotency_key,
                                                                      request))
            if "limit" not in e.failed:
                raise
            raise PlanLimitReached(f"Your {user.plan} plan allows {max_orders} orders. Upgrade for more.")
        return order

    def get_order(self, user: CurrentUser, order_id: str) -> Order:
        order = self.orders.get(user.require_tenant(), order_id)
        if not order:
            raise NotFound(f"Order {order_id} not found")
        return order

    def list_orders(self, user: CurrentUser, limit: int, cursor: str | None = None) -> tuple[list[Order], str | None]:
        """Newest first. Returns the orders and a cursor for the next page (None at the end)."""
        return self.orders.list(user.require_tenant(), limit, cursor)

    def change_status(self, user: CurrentUser, order_id: str, new_status: OrderStatus) -> Order:
        order = self.get_order(user, order_id)
        if new_status not in ALLOWED_TRANSITIONS[order.status]:
            raise InvalidRequest(f"Cannot change order from '{order.status.value}' to '{new_status.value}'")
        if not self.orders.update_status(order.tenant_id, order_id, new_status, expected=order.status):
            raise Conflict("The order was changed by someone else. Reload it and try again.")
        return order.model_copy(update={"status": new_status})
