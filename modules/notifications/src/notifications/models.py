import time
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def new_notification_id() -> str:
    # Starts with a timestamp so ids sort by time in DynamoDB.
    return f"{int(time.time() * 1000):013d}-{uuid4().hex[:8]}"


class Notification(BaseModel):
    notification_id: str = Field(default_factory=new_notification_id)
    tenant_id: str
    user_id: str
    title: str
    body: str
    read: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
