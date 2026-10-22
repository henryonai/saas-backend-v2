"""
What the orders HTTP API accepts and returns (its contract with clients,
also the source of the OpenAPI document). Stored data is in models.py.

A single order is returned as the Order model; add an OrderOut schema here as
soon as stored and returned fields need to differ.
"""
from pydantic import BaseModel, Field

from orders.models import Order, OrderStatus


# ---------- requests ----------

class OrderCreate(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=100)
    price_cents: int = Field(ge=0)  # money as integer cents, never float


class OrderUpdateStatus(BaseModel):
    status: OrderStatus


# ---------- responses ----------

class OrderPage(BaseModel):
    items: list[Order]
    next_cursor: str | None = None  # pass back as ?cursor= to get the next page
