"""
Billing rules. The plan changes only when the provider's WEBHOOK arrives,
never when the browser returns from checkout (the user may close the tab).

Each webhook is applied with one conditional DynamoDB transaction (new state
+ its events), so it is all-or-nothing: a timeout or crash can't leave it
half done, and the provider's retry is either applied or skipped as already
seen.

Providers retry and don't keep order, so plan and status each remember the
provider event that last set them. An event only changes a field if it is
newer than that event (same second: arrival order, unless it is the same
event again). A late "activated" can still set the plan after a newer
"payment failed", but can't undo a newer "cancelled". A newer event that
leaves plan and status as they are only moves those markers (and the
revision): it publishes nothing, so the many updates Stripe sends don't
notify anyone again. Listeners only compare revisions, so gaps are fine.
"""
from aws_lambda_powertools import Logger

from shared.auth import ROLE_OWNER, CurrentUser
from shared.dynamo import ConditionFailed
from shared.errors import Conflict, InvalidRequest, ServiceBusy
from shared.plans import DEFAULT_PLAN, is_plan

from billing import events
from billing.models import Subscription
from billing.provider import BillingEvent, PaymentProvider
from billing.repository import SubscriptionRepository

logger = Logger(child=True)


class BillingService:
    def __init__(self, subscriptions: SubscriptionRepository, provider: PaymentProvider):
        self.subscriptions = subscriptions
        self.provider = provider

    def get_subscription(self, user: CurrentUser) -> Subscription:
        """The tenant's subscription (Free until the first webhook arrives)."""
        tenant_id = user.require_tenant()
        return self.subscriptions.get(tenant_id) or Subscription(tenant_id=tenant_id)

    def start_checkout(self, user: CurrentUser, plan: str) -> str:
        """The page where the owner pays for a plan (or confirms a plan change)."""
        tenant_id = user.require_role(ROLE_OWNER)  # only the owner pays
        self.subscriptions.save_owner(tenant_id, user.user_id, user.email)
        sub = self.subscriptions.get(tenant_id) or Subscription(tenant_id=tenant_id)
        if sub.plan == plan and sub.status == "active":
            raise Conflict(f"The workspace is already on the {plan} plan")
        return self.provider.create_checkout(sub, plan, user.email)

    def open_portal(self, user: CurrentUser) -> str:
        """The provider's page for the card, invoices, plan changes and cancelling."""
        tenant_id = user.require_role(ROLE_OWNER)
        return self.provider.create_portal(self.subscriptions.get(tenant_id) or Subscription(tenant_id=tenant_id))

    def handle_webhook(self, headers: dict, raw_body: str) -> None:
        event = self.provider.parse_webhook(headers, raw_body)  # raises on bad signature
        change = self._fields_for(event)
        if change is None:
            logger.info("Unhandled billing event", extra={"type": event.event_type})
            return

        current = self.subscriptions.get(event.tenant_id) or Subscription(tenant_id=event.tenant_id)
        newer = {field: value for field, value in change.items() if self._is_newer(event, current, field)}
        if not newer:
            logger.info("Old or repeated webhook skipped", extra={"event_id": event.event_id})
            return

        # Every save moves the revision on, so a webhook that read the state
        # before this one can't overwrite it (save_change's condition).
        updates = {"revision": current.revision + 1}
        for field in newer:
            updates[f"{field}_changed_at"] = event.occurred_at
            updates[f"{field}_event_id"] = event.event_id
        for name in ("customer_id", "subscription_id"):
            if getattr(event, name):
                updates[name] = getattr(event, name)
        changed = {field: value for field, value in newer.items() if getattr(current, field) != value}
        updates.update(changed)
        new = current.model_copy(update=updates)

        to_publish = [events.subscription_changed(new)] if changed else []
        if changed.get("status") == "past_due":
            to_publish.append(events.payment_failed(new))
        try:
            self.subscriptions.save_change(new, current.revision, to_publish)
        except ConditionFailed:
            # Another webhook for this tenant was saved in the meantime. Fail
            # with a 5xx so the provider retries; the retry sees the newer state.
            raise ServiceBusy("Subscription changed during processing, retry later")

    @staticmethod
    def _fields_for(event: BillingEvent) -> dict | None:
        """The fields this event sets, or None if billing doesn't handle it."""
        if event.event_type == "subscription.activated":
            if not is_plan(event.plan):
                raise InvalidRequest(f"Unknown plan {event.plan!r} in webhook")
            return {"plan": event.plan, "status": "active"}
        if event.event_type == "subscription.cancelled":
            return {"plan": DEFAULT_PLAN, "status": "cancelled"}
        if event.event_type == "payment.failed":
            return {"status": "past_due"}  # keep the plan during a grace period
        return None

    @staticmethod
    def _is_newer(event: BillingEvent, current: Subscription, field: str) -> bool:
        changed_at = getattr(current, f"{field}_changed_at")
        event_id = getattr(current, f"{field}_event_id")
        return event.occurred_at > changed_at or (event.occurred_at == changed_at and event.event_id != event_id)
