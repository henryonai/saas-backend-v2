"""
Events the orders module publishes: its public contract, in one place.

Listeners in other modules never import this file (no module imports
another); they match on the event name. So keep names stable and change
payloads only by adding fields. The README's events table lists every event.
"""
from shared.events import Event

from orders.models import Order

ORDER_CREATED = "OrderCreated"


def order_created(order: Order) -> Event:
    return Event(ORDER_CREATED, order.tenant_id, {
        "order_id": order.order_id,
        "user_id": order.created_by,
        "item": order.item,
    })
