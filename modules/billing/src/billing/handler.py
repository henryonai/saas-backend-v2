"""Lambda entry point for the billing HTTP API (/billing)."""
from shared.http import create_app, create_handler

from billing.api import router

app = create_app(router, title="Billing API")
handler = create_handler(app)
