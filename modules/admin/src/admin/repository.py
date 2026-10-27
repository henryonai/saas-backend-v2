"""
Admin table:
  pk = TENANTS              sk = <tenant_id>        a workspace's summary (models.WorkspaceSummary)
  pk = EVENT#<event_id>     sk = EVENT              an event already applied to the summaries (expires)
  pk = LOG                  sk = <at>#<action_id>   something a platform admin did (models.AdminAction)
  pk = OUTBOX#<event_id>    sk = OUTBOX             events to publish (shared/outbox.py)

The summaries are a read model: events from other modules update them, each
in one transaction with its EVENT# marker, so a repeated event changes nothing.
Every workspace is in one partition, read by platform admins only; fine for
thousands of workspaces, page through it.
"""
import base64
import json
import time

from boto3.dynamodb.conditions import Key

from shared.dynamo import ConditionFailed, transact
from shared.events import Event
from shared.outbox import outbox_put

from admin.models import AdminAction, WorkspaceSummary

TENANTS = "TENANTS"
MARKER_SECONDS = 7 * 24 * 3600   # longer than events are redelivered


class InvalidCursor(ValueError):
    pass


def _cursor(last: dict | None) -> str | None:
    return base64.urlsafe_b64encode(json.dumps([last["sk"]]).encode()).decode().rstrip("=") if last else None


def _start(pk: str, cursor: str | None) -> dict:
    if not cursor:
        return {}
    try:
        (sk,) = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as e:
        raise InvalidCursor(cursor) from e
    if not isinstance(sk, str):
        raise InvalidCursor(cursor)
    return {"ExclusiveStartKey": {"pk": pk, "sk": sk}}


def _summary(item: dict) -> WorkspaceSummary:
    return WorkspaceSummary.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk")})


class AdminRepository:
    def __init__(self, table):
        self.table = table

    # ---------- the read model ----------

    def apply(self, event_id: str, update: dict | None) -> bool:
        """
        Applies one event's change to a summary (an Update or Delete operation,
        or None for no change), once. False if it was applied already, or its
        condition failed (an older plan revision): then only the marker is saved.
        """
        marker = {"Put": {"Item": {"pk": f"EVENT#{event_id}", "sk": "EVENT",
                                   "expires_at": int(time.time()) + MARKER_SECONDS},
                          "ConditionExpression": "attribute_not_exists(pk)"}}
        try:
            transact(self.table, marker=marker, summary=update)
            return True
        except ConditionFailed as e:
            if "marker" in e.failed:
                return False
            transact(self.table, marker=marker)   # stale: remember it, change nothing
            return False

    def get(self, tenant_id: str) -> WorkspaceSummary | None:
        item = self.table.get_item(Key={"pk": TENANTS, "sk": tenant_id}, ConsistentRead=True).get("Item")
        return _summary(item) if item else None

    def page(self, limit: int, cursor: str | None = None) -> tuple[list[WorkspaceSummary], str | None]:
        result = self.table.query(KeyConditionExpression=Key("pk").eq(TENANTS), Limit=limit,
                                  **_start(TENANTS, cursor))
        return [_summary(i) for i in result["Items"]], _cursor(result.get("LastEvaluatedKey"))

    def save(self, summary: WorkspaceSummary) -> None:
        """Writes a whole summary (the backfill script)."""
        self.table.put_item(Item={"pk": TENANTS, "sk": summary.tenant_id, **summary.model_dump()})

    # ---------- what admins do ----------

    def act(self, action: AdminAction, event: Event) -> None:
        """Logs the action and saves its event, in one transaction."""
        transact(
            self.table,
            log={"Put": {"Item": {"pk": "LOG", "sk": f"{action.at}#{action.action_id}", **action.model_dump()}}},
            event=outbox_put(event),
        )

    def log(self, limit: int, cursor: str | None = None) -> tuple[list[AdminAction], str | None]:
        """Newest first."""
        result = self.table.query(KeyConditionExpression=Key("pk").eq("LOG"), ScanIndexForward=False, Limit=limit,
                                  **_start("LOG", cursor))
        actions = [AdminAction.model_validate({k: v for k, v in i.items() if k not in ("pk", "sk")})
                   for i in result["Items"]]
        return actions, _cursor(result.get("LastEvaluatedKey"))
