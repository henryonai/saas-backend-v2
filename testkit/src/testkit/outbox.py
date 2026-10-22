"""Reads the events a module saved in its outbox (see shared/outbox.py)."""
import json


def outbox_events(table) -> list[dict]:
    """Events waiting in the table's outbox, oldest first: [{"type", "detail"}]."""
    items = table.scan()["Items"]
    events = [{"type": i["event_type"], "detail": json.loads(i["detail"])}
              for i in items if i["pk"].startswith("OUTBOX#")]
    return sorted(events, key=lambda e: (e["detail"]["occurred_at"], e["type"]))


def event_types(table) -> list[str]:
    return [e["type"] for e in outbox_events(table)]
