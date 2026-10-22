from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
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
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    created_by: str
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
