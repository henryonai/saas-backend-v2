"""
What the orders module stores and works with. The repository saves these;
the service returns them. What the API accepts and returns is in schemas.py.
"""
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from shared.ids import sortable_id


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class Order(BaseModel):
    order_id: str = Field(default_factory=sortable_id)  # sorts by creation time
    tenant_id: str
    created_by: str
    item: str
    quantity: int
    price_cents: int  # money as integer cents, never float
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
