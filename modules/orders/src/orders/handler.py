"""Lambda entry point for the orders HTTP API (/orders)."""
from shared.http import create_app, create_handler

from orders.api import router

app = create_app(router, title="Orders API")
handler = create_handler(app)
