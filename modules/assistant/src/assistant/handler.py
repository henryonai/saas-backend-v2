"""Lambda entry point for the assistant's HTTP API (/assistant/conversations). The agent itself runs on AgentCore."""
from shared.http import create_app, create_handler

from assistant.api import router

app = create_app(router, title="Assistant API")
handler = create_handler(app)
