"""
What the assistant module stores: one usage record per workspace and month.
What the agent endpoint accepts and streams back is in schemas.py.
"""
from datetime import UTC, datetime

from pydantic import BaseModel


def current_month() -> str:
    """The usage period: a calendar month in UTC, e.g. "2026-10"."""
    return datetime.now(UTC).strftime("%Y-%m")


class Usage(BaseModel):
    tenant_id: str
    month: str
    messages: int = 0         # messages sent to the assistant
    tokens: int = 0           # input + output; what the plan's token cap counts
    input_tokens: int = 0
    output_tokens: int = 0
    sandbox_sessions: int = 0  # terminals the bash tool started
