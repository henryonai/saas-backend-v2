from datetime import UTC, datetime

from pydantic import BaseModel, Field

from shared.ids import sortable_id


class Notification(BaseModel):
    # Starts with a timestamp so ids sort by time in DynamoDB.
    notification_id: str = Field(default_factory=sortable_id)
    tenant_id: str
    user_id: str
    title: str
    body: str
    read: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
