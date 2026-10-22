"""
Events are how modules talk to each other.

Services receive an EventPublisher (dependency injection) instead of calling
a global function, so tests can pass a fake publisher.

Every event includes tenant_id. Include everything listeners need ("fat
events"), so they never have to call back into your module.
"""
import json
from typing import Protocol

EVENT_SOURCE = "saas.app"


class EventPublisher(Protocol):
    def publish(self, event_type: str, tenant_id: str, **data) -> None: ...


class EventBridgePublisher:
    def __init__(self, client, bus_name: str):
        self.client = client
        self.bus_name = bus_name

    def publish(self, event_type: str, tenant_id: str, **data) -> None:
        self.client.put_events(
            Entries=[{
                "EventBusName": self.bus_name,
                "Source": EVENT_SOURCE,
                "DetailType": event_type,
                "Detail": json.dumps({"tenant_id": tenant_id, **data}),
            }]
        )
