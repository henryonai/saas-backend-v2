"""
Billing table:
  pk = TENANT#<tenant_id>   sk = SUBSCRIPTION   subscription (while its workspace is being deleted:
                                                deleting, deleting_at, renewal_stopped; once it is
                                                purged: deleted_at, and TTL removes it after
                                                DELETED_KEEP_SECONDS)
  pk = TENANT#<tenant_id>   sk = STATUS         the workspace's status (no item: active)
                                                (shared/workspace_status.py)
  pk = OUTBOX#<event_id>    sk = OUTBOX         events to publish (shared/outbox.py)
"""
from botocore.exceptions import ClientError

from shared.dynamo import is_condition_failure, transact
from shared.events import Event
from shared.outbox import outbox_put
from shared.workspace_status import STATUS_SK, check_active, save

from billing.models import Subscription

# How long a purged workspace's subscription stays, ignoring the provider's webhooks.
DELETED_KEEP_SECONDS = 90 * 24 * 3600


def _key(tenant_id: str) -> dict:
    return {"pk": f"TENANT#{tenant_id}", "sk": "SUBSCRIPTION"}


class SubscriptionRepository:
    def __init__(self, table):
        self.table = table

    def get(self, tenant_id: str) -> Subscription | None:
        item = self.table.get_item(Key=_key(tenant_id), ConsistentRead=True).get("Item")
        if not item:
            return None
        return Subscription.model_validate({k: v for k, v in item.items() if k not in ("pk", "sk")})

    def save_status(self, event_type: str, detail: dict) -> None:
        save(self.table, event_type, detail)

    def check_active(self, tenant_id: str) -> None:
        """Raises WorkspaceInactive (403) while the workspace is suspended or being deleted."""
        check_active(self.table, tenant_id)

    def mark_deleted(self, tenant_id: str, now: int) -> None:
        """
        The workspace was purged: the subscription is cancelled and forgets
        its owner; TTL removes it later. Creates the record if there was none.
        """
        self.table.update_item(
            Key=_key(tenant_id),
            UpdateExpression=("SET tenant_id = :t, #p = :free, #s = :cancelled, deleted_at = :now, "
                              "expires_at = :expires, owner_id = :none, owner_email = :none"),
            ExpressionAttributeNames={"#p": "plan", "#s": "status"},
            ExpressionAttributeValues={":t": tenant_id, ":free": "free", ":cancelled": "cancelled", ":now": now,
                                       ":expires": now + DELETED_KEEP_SECONDS, ":none": ""},
        )
        self.table.delete_item(Key={"pk": f"TENANT#{tenant_id}", "sk": STATUS_SK})

    def save_deleting(self, tenant_id: str, deleting: bool, at: str) -> bool:
        """
        Saves whether the workspace is being deleted, as of `at` (the event's
        occurred_at). False if there is no subscription, or a newer event was
        saved already. The same event again is saved again (a retry).
        """
        try:
            self.table.update_item(
                Key=_key(tenant_id),
                UpdateExpression="SET deleting = :deleting, deleting_at = :at",
                ConditionExpression=("attribute_exists(pk)"
                                     " AND (attribute_not_exists(deleting_at) OR deleting_at <= :at)"),
                ExpressionAttributeValues={":deleting": deleting, ":at": at},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def save_renewal_stopped(self, tenant_id: str, stopped: bool) -> None:
        """What billing did to the provider's renewal: saved as soon as it's done."""
        self.table.update_item(Key=_key(tenant_id), UpdateExpression="SET renewal_stopped = :stopped",
                               ExpressionAttributeValues={":stopped": stopped})

    def save_owner(self, tenant_id: str, owner_id: str, owner_email: str) -> None:
        """Creates the record if missing; never overwrites plan, status or revision."""
        self.table.update_item(
            Key=_key(tenant_id),
            UpdateExpression=("SET tenant_id = :t, owner_id = :oid, owner_email = :oem, "
                              "#p = if_not_exists(#p, :free), #s = if_not_exists(#s, :active), "
                              "revision = if_not_exists(revision, :zero)"),
            ExpressionAttributeNames={"#p": "plan", "#s": "status"},
            ExpressionAttributeValues={":t": tenant_id, ":oid": owner_id, ":oem": owner_email,
                                       ":free": "free", ":active": "active", ":zero": 0},
        )

    def save_change(self, sub: Subscription, expected_revision: int, events: list[Event]) -> None:
        """
        Saves the new plan/status (and the provider's ids) and its events in one transaction, only if
        nobody changed the subscription since it was read at expected_revision.
        Raises ConditionFailed({"subscription"}) otherwise.
        """
        update = {"Update": {
            "Key": _key(sub.tenant_id),
            "UpdateExpression": ("SET tenant_id = :t, #p = :p, #s = :s, revision = :rev, "
                                 "plan_changed_at = :pat, plan_event_id = :pid, "
                                 "status_changed_at = :sat, status_event_id = :sid, "
                                 "customer_id = :cid, subscription_id = :subid, comped = :comped"),
            "ConditionExpression": ("attribute_not_exists(pk) OR attribute_not_exists(revision)"
                                    " OR revision = :expected"),
            "ExpressionAttributeNames": {"#p": "plan", "#s": "status"},
            "ExpressionAttributeValues": {
                ":t": sub.tenant_id, ":p": sub.plan, ":s": sub.status, ":rev": sub.revision,
                ":pat": sub.plan_changed_at, ":pid": sub.plan_event_id,
                ":sat": sub.status_changed_at, ":sid": sub.status_event_id, ":expected": expected_revision,
                ":cid": sub.customer_id, ":subid": sub.subscription_id, ":comped": sub.comped,
            },
        }}
        events_ops = {f"event_{i}": outbox_put(e) for i, e in enumerate(events)}
        transact(self.table, subscription=update, **events_ops)
