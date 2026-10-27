"""
Billing table:
  pk = TENANT#<tenant_id>   sk = SUBSCRIPTION   subscription (once its workspace is purged: deleted_at,
                                                and TTL removes it after DELETED_KEEP_SECONDS)
  pk = OUTBOX#<event_id>    sk = OUTBOX         events to publish (shared/outbox.py)
"""
from shared.dynamo import transact
from shared.events import Event
from shared.outbox import outbox_put

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
                                 "customer_id = :cid, subscription_id = :subid"),
            "ConditionExpression": ("attribute_not_exists(pk) OR attribute_not_exists(revision)"
                                    " OR revision = :expected"),
            "ExpressionAttributeNames": {"#p": "plan", "#s": "status"},
            "ExpressionAttributeValues": {
                ":t": sub.tenant_id, ":p": sub.plan, ":s": sub.status, ":rev": sub.revision,
                ":pat": sub.plan_changed_at, ":pid": sub.plan_event_id,
                ":sat": sub.status_changed_at, ":sid": sub.status_event_id, ":expected": expected_revision,
                ":cid": sub.customer_id, ":subid": sub.subscription_id,
            },
        }}
        events_ops = {f"event_{i}": outbox_put(e) for i, e in enumerate(events)}
        transact(self.table, subscription=update, **events_ops)
