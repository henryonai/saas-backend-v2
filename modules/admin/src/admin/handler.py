"""Lambda entry point for the admin HTTP API (/admin)."""
from shared.http import create_app, create_handler

from admin.api import router

app = create_app(router, title="Admin API")
handler = create_handler(app)
