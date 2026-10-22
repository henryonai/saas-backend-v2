"""Composition root: the one place that picks real implementations."""
from functools import lru_cache

import boto3

from orders.repository import OrderRepository
from orders.service import OrderService
from orders.settings import OrdersSettings


@lru_cache
def get_settings() -> OrdersSettings:
    return OrdersSettings()


@lru_cache
def get_order_service() -> OrderService:
    settings = get_settings()
    return OrderService(orders=OrderRepository(boto3.resource("dynamodb").Table(settings.orders_table)))
