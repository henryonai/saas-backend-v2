"""
Events are how modules talk to each other.

A service never sends an event directly. It creates an Event, and its
repository saves the event in the same DynamoDB transaction as the change
(the "outbox", see outbox.py). The outbox relay then publishes it to
EventBridge. So an event is published if, and only if, the change was saved.

Every event includes tenant_id, a unique event_id (listeners use it to skip
duplicates) and occurred_at. Include everything listeners need ("fat
events"), so they never have to call back into your module.
"""
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

EVENT_SOURCE = "saas.app"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Event:
    type: str
    tenant_id: str
    data: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=now_iso)

    def detail(self) -> dict:
        return {"tenant_id": self.tenant_id, "event_id": self.event_id,
                "occurred_at": self.occurred_at, **self.data}


class EventPublisher(Protocol):
    def publish(self, event_type: str, detail: dict) -> None: ...


class EventBridgePublisher:
    def __init__(self, client, bus_name: str):
        self.client = client
        self.bus_name = bus_name

    def publish(self, event_type: str, detail: dict) -> None:
        response = self.client.put_events(
            Entries=[{
                "EventBusName": self.bus_name,
                "Source": EVENT_SOURCE,
                "DetailType": event_type,
                "Detail": json.dumps(detail),
            }]
        )
        # PutEvents returns HTTP 200 even when an entry is rejected (for
        # example when throttled), so check every response.
        if response.get("FailedEntryCount"):
            entry = response["Entries"][0]
            raise RuntimeError(f"EventBridge rejected {event_type}: "
                               f"{entry.get('ErrorCode')} {entry.get('ErrorMessage')}")
