"""Lambda entry point for the notifications HTTP API (/notifications)."""
from shared.http import create_app, create_handler

from notifications.api import router

app = create_app(router, title="Notifications API")
handler = create_handler(app)
