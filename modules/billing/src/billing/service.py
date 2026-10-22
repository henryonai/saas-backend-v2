"""
Billing rules. The plan changes only when the provider's WEBHOOK arrives,
never when the browser returns from checkout (the user may close the tab).
"""
from aws_lambda_powertools import Logger

from shared.auth import ROLE_OWNER, CurrentUser
from shared.events import EventPublisher
from shared.plans import DEFAULT_PLAN, PLANS

from billing.models import BillingSummary, Subscription
from billing.provider import BillingEvent, PaymentProvider
from billing.repository import ProcessedEventRepository, SubscriptionRepository

logger = Logger(child=True)


class BillingService:
    def __init__(self, subscriptions: SubscriptionRepository, processed: ProcessedEventRepository,
                 provider: PaymentProvider, publisher: EventPublisher):
        self.subscriptions = subscriptions
        self.processed = processed
        self.provider = provider
        self.publisher = publisher

    def summary(self, user: CurrentUser) -> BillingSummary:
        tenant_id = user.require_tenant()
        sub = self.subscriptions.get(tenant_id) or Subscription(tenant_id=tenant_id)
        plan = PLANS[sub.plan]
        return BillingSummary(plan=sub.plan, plan_name=plan["name"],
                              price_usd_month=plan["price_usd_month"], status=sub.status,
                              limits={k: v for k, v in plan.items() if k.startswith("max_")})

    def start_checkout(self, user: CurrentUser, plan: str) -> str:
        tenant_id = user.require_role(ROLE_OWNER)  # only the owner pays
        self.subscriptions.save_owner(tenant_id, user.user_id, user.email)
        return self.provider.create_checkout(tenant_id, plan, user.email)

    def handle_webhook(self, headers: dict, raw_body: str) -> None:
        event = self.provider.parse_webhook(headers, raw_body)  # raises on bad signature

        if not self.processed.mark(event.event_id):
            logger.info("Duplicate webhook ignored", extra={"event_id": event.event_id})
            return
        try:
            self._apply(event)
        except Exception:
            self.processed.unmark(event.event_id)  # let the provider's retry run again
            raise

    def _apply(self, event: BillingEvent) -> None:
        current = self.subscriptions.get(event.tenant_id) or Subscription(tenant_id=event.tenant_id)
        owner = {"owner_id": current.owner_id, "owner_email": current.owner_email}

        if event.event_type == "subscription.activated":
            plan, status = event.plan, "active"
        elif event.event_type == "subscription.cancelled":
            plan, status = DEFAULT_PLAN, "cancelled"
        elif event.event_type == "payment.failed":
            plan, status = current.plan, "past_due"  # keep the plan during a grace period
        else:
            logger.info("Unhandled billing event", extra={"type": event.event_type})
            return

        self.subscriptions.update(event.tenant_id, plan, status)
        self.publisher.publish("SubscriptionChanged", event.tenant_id, plan=plan, status=status, **owner)
        if status == "past_due":
            self.publisher.publish("PaymentFailed", event.tenant_id, **owner)
