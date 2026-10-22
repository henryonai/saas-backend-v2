from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from shared.ids import sortable_id


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class OrderCreate(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=100)
    price_cents: int = Field(ge=0)  # money as integer cents, never float


class OrderUpdateStatus(BaseModel):
    status: OrderStatus


class Order(OrderCreate):
    order_id: str = Field(default_factory=sortable_id)  # sorts by creation time
    tenant_id: str
    created_by: str
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class OrderPage(BaseModel):
    items: list[Order]
    next_cursor: str | None = None  # pass back as ?cursor= to get the next page
