"""Lambda entry point for the audit HTTP API (/audit)."""
from shared.http import create_app, create_handler

from audit.api import router

app = create_app(router, title="Audit API")
handler = create_handler(app)
