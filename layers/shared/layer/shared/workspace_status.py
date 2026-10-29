"""
Whether a workspace takes changes: a copy of its status in a module's own table.

Accounts owns a workspace's status. A suspended workspace, or one being
deleted, leaves its members' tokens only when they next refresh (up to 15
minutes), so a module that changes a workspace's data keeps this copy, kept
up to date from the events below, and refuses changes while it isn't active.
The copy lags accounts by the seconds an event takes; accounts itself checks
the workspace's own status.

    TENANT#<tenant_id> / STATUS   {status, at}   (no item: active)

It stays "active" after an unsuspend or a restore (a late event must not undo it);
a purged workspace's partition goes with TenantDeleted, this item included.
"""
from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure
from shared.errors import Forbidden

# accounts' and admin's events, by name (modules never import each other).
STATUS_EVENTS = {
    "TenantSuspended": "suspended",
    "TenantUnsuspended": "active",
    "TenantDeletionScheduled": "deleting",
    "TenantRestored": "active",
}
STATUS_SK = "STATUS"


class WorkspaceInactive(Forbidden):
    """403: the workspace is suspended or being deleted."""

    MESSAGES = {
        "suspended": "This workspace is suspended. Contact support to use it again.",
        "deleting": "This workspace is being deleted. Its owner can restore it.",
    }

    def __init__(self, status: str):
        super().__init__(self.MESSAGES.get(status, "This workspace can't be changed right now."))
        self.status = status


def require_active(status: str) -> None:
    """Raises WorkspaceInactive unless `status` is "active"."""
    if status != "active":
        raise WorkspaceInactive(status)


def save(table, event_type: str, detail: dict) -> None:
    """
    Saves the status a STATUS_EVENTS event sets. Events can arrive out of
    order (a suspend and an unsuspend seconds apart), so an event older than
    the saved one changes nothing.
    """
    try:
        table.put_item(
            Item={"pk": f"TENANT#{detail['tenant_id']}", "sk": STATUS_SK,
                  "status": STATUS_EVENTS[event_type], "at": detail["occurred_at"]},
            ConditionExpression="attribute_not_exists(pk) OR #at < :at",
            ExpressionAttributeNames={"#at": "at"},
            ExpressionAttributeValues={":at": detail["occurred_at"]},
        )
    except ClientError as e:
        if not is_condition_failure(e):
            raise


def status_of(table, tenant_id: str) -> str:
    item = table.get_item(Key={"pk": f"TENANT#{tenant_id}", "sk": STATUS_SK}, ConsistentRead=True).get("Item")
    return item["status"] if item else "active"


def check_active(table, tenant_id: str) -> None:
    """Raises WorkspaceInactive if the module's copy says the workspace isn't active."""
    require_active(status_of(table, tenant_id))


def active_condition(table, tenant_id: str) -> dict:
    """
    A transaction operation (shared.dynamo.transact) that fails while the
    workspace isn't active, for writes that are a transaction already. On
    ConditionFailed with its name, call check_active to raise the right error.
    """
    return {"ConditionCheck": {
        "Key": {"pk": f"TENANT#{tenant_id}", "sk": STATUS_SK},
        "ConditionExpression": "attribute_not_exists(pk) OR #status = :active",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {":active": "active"},
    }}
