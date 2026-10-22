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


class FakeIdentity:
    """Stands in for Cognito: returns predictable user ids."""

    def __init__(self):
        self.created: list[str] = []

    def create_user(self, email: str) -> str:
        self.created.append(email)
        return "user-" + email.split("@")[0]


class FakeEmailSender:
    """Records emails instead of sending them with SES."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
