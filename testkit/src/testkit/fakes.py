"""Fake versions of external dependencies, injected in tests instead of AWS."""


class FakePublisher:
    """Records events instead of sending them to EventBridge."""

    def __init__(self):
        self.events: list[dict] = []

    def publish(self, event_type: str, tenant_id: str, **data) -> None:
        self.events.append({"type": event_type, "detail": {"tenant_id": tenant_id, **data}})

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def last(self) -> dict:
        return self.events[-1]
