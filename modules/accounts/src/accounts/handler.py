"""Lambda entry point for the accounts HTTP API (/tenants, /users)."""
from shared.http import create_app, create_handler

from accounts.api import router

app = create_app(router, title="Accounts API")
handler = create_handler(app)
