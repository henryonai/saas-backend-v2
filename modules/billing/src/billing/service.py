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
"payment failed", but can't undo a newer "cancelled".
"""
from aws_lambda_powertools import Logger

from shared.auth import ROLE_OWNER, CurrentUser
from shared.dynamo import ConditionFailed
from shared.errors import InvalidRequest, ServiceBusy
from shared.plans import DEFAULT_PLAN, get_plan, is_plan

from billing import events
from billing.models import BillingSummary, Subscription
from billing.provider import BillingEvent, PaymentProvider
from billing.repository import SubscriptionRepository

logger = Logger(child=True)


class BillingService:
    def __init__(self, subscriptions: SubscriptionRepository, provider: PaymentProvider):
        self.subscriptions = subscriptions
        self.provider = provider

    def summary(self, user: CurrentUser) -> BillingSummary:
        tenant_id = user.require_tenant()
        sub = self.subscriptions.get(tenant_id) or Subscription(tenant_id=tenant_id)
        plan = get_plan(sub.plan)
        return BillingSummary(plan=sub.plan, plan_name=plan["name"],
                              price_usd_month=plan["price_usd_month"], status=sub.status,
                              limits={k: v for k, v in plan.items() if k.startswith("max_")})

    def start_checkout(self, user: CurrentUser, plan: str) -> str:
        tenant_id = user.require_role(ROLE_OWNER)  # only the owner pays
        self.subscriptions.save_owner(tenant_id, user.user_id, user.email)
        return self.provider.create_checkout(tenant_id, plan, user.email)

    def handle_webhook(self, headers: dict, raw_body: str) -> None:
        event = self.provider.parse_webhook(headers, raw_body)  # raises on bad signature
        change = self._fields_for(event)
        if change is None:
            logger.info("Unhandled billing event", extra={"type": event.event_type})
            return

        current = self.subscriptions.get(event.tenant_id) or Subscription(tenant_id=event.tenant_id)
        applied = {field: value for field, value in change.items() if self._is_newer(event, current, field)}
        if not applied:
            logger.info("Old or repeated webhook skipped", extra={"event_id": event.event_id})
            return

        updates = {**applied, "revision": current.revision + 1}
        for field in applied:
            updates[f"{field}_changed_at"] = event.occurred_at
            updates[f"{field}_event_id"] = event.event_id
        new = current.model_copy(update=updates)

        to_publish = [events.subscription_changed(new)]
        if applied.get("status") == "past_due":
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
