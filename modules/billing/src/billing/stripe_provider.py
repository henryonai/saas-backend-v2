"""
Stripe as the payment provider (see provider.PaymentProvider).

- A workspace's first subscription goes through Stripe Checkout, with the
  tenant_id in the subscription's metadata.
- Everything after that happens in Stripe's customer portal: changing plan,
  the card, invoices, cancelling. A plan change opens the portal's "confirm"
  page, which handles proration and card authentication; a second Checkout
  would start a second subscription instead.
- Webhooks: Stripe's signature and its timestamp (5 minutes) are checked,
  then the subscription is fetched again, so billing always saves Stripe's
  current state, in whatever order the events arrive.

scripts/stripe-setup.py creates the prices, the portal configuration and the
webhook endpoint, and saves what this class needs in SSM.
"""
import json
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

import stripe
from aws_lambda_powertools import Logger

from shared.errors import InvalidRequest, ServiceBusy

from billing.models import Subscription
from billing.provider import BillingEvent

logger = Logger(child=True)

# The webhook endpoint's events (stripe-setup.py subscribes to these).
EVENTS = ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"]

# A Stripe subscription status, as a billing event. "incomplete" (the first
# payment isn't done yet) and "paused" change nothing.
EVENT_FOR_STATUS = {
    "active": "subscription.activated",
    "trialing": "subscription.activated",
    "past_due": "payment.failed",
    "unpaid": "payment.failed",
    "canceled": "subscription.cancelled",
    "incomplete_expired": "subscription.cancelled",
}


@dataclass(frozen=True)
class StripeConfig:
    """What stripe-setup.py saves in SSM (StripeConfigParam)."""
    prices: dict[str, str]          # plan -> the price new subscriptions use
    plan_for_price: dict[str, str]  # every price of a plan (old ones too) -> plan
    portal_configuration: str

    @classmethod
    def parse(cls, raw: str) -> "StripeConfig":
        data = json.loads(raw)
        return cls(prices=data["prices"], plan_for_price=data["plan_for_price"],
                   portal_configuration=data["portal_configuration"])


def new_client(api_key: str) -> stripe.StripeClient:
    # Short timeouts: the API function has 10 seconds in all.
    return stripe.StripeClient(api_key, max_network_retries=1, http_client=stripe.RequestsClient(timeout=(3, 5)))


class StripePaymentProvider:
    def __init__(self, client: Callable[[], stripe.StripeClient], config: Callable[[], StripeConfig],
                 webhook_secret: Callable[[], str], app_url: str):
        # Functions, so each value is read from SSM only when it's needed.
        self.client = client
        self.config = config
        self.webhook_secret = webhook_secret
        self.return_url = f"{app_url}/billing"   # the billing page waits for the webhook

    def create_checkout(self, sub: Subscription, plan: str, customer_email: str) -> str:
        price = self.config().prices.get(plan)
        if not price:
            raise InvalidRequest(f"No Stripe price for the {plan} plan; run scripts/stripe-setup.py")
        if sub.subscription_id and sub.status in ("active", "past_due"):
            return self._change_plan(sub, price)
        params = {
            "mode": "subscription",
            "line_items": [{"price": price, "quantity": 1}],
            "client_reference_id": sub.tenant_id,
            "subscription_data": {"metadata": {"tenant_id": sub.tenant_id}},
            "success_url": self.return_url,
            "cancel_url": self.return_url,
        }
        if sub.customer_id:
            params["customer"] = sub.customer_id   # subscribing again: same Stripe customer
        else:
            params["customer_email"] = customer_email
        with stripe_errors("start a checkout"):
            return self.client().v1.checkout.sessions.create(params).url

    def _change_plan(self, sub: Subscription, price: str) -> str:
        with stripe_errors("change the plan"):
            current = self.client().v1.subscriptions.retrieve(sub.subscription_id)
            item = current["items"]["data"][0]
            return self.client().v1.billing_portal.sessions.create({
                "customer": sub.customer_id,
                "configuration": self.config().portal_configuration,
                "return_url": self.return_url,
                "flow_data": {
                    "type": "subscription_update_confirm",
                    "subscription_update_confirm": {
                        "subscription": sub.subscription_id,
                        "items": [{"id": item["id"], "price": price, "quantity": 1}],
                    },
                    "after_completion": {"type": "redirect", "redirect": {"return_url": self.return_url}},
                },
            }).url

    def create_portal(self, sub: Subscription) -> str:
        if not sub.customer_id:
            raise InvalidRequest("This workspace has no billing account yet")
        with stripe_errors("open the billing portal"):
            return self.client().v1.billing_portal.sessions.create({
                "customer": sub.customer_id,
                "configuration": self.config().portal_configuration,
                "return_url": self.return_url,
            }).url

    def cancel(self, sub: Subscription) -> None:
        """
        Cancels the Stripe subscription now, without a refund for the rest of
        the period (change that here if you prorate). The customer and its
        invoices stay in Stripe.
        """
        if not sub.subscription_id:
            return
        with stripe_errors("cancel the subscription"):
            current = self.client().v1.subscriptions.retrieve(sub.subscription_id)
            if current.status not in ("canceled", "incomplete_expired"):
                self.client().v1.subscriptions.cancel(sub.subscription_id)

    def parse_webhook(self, headers: dict, raw_body: str) -> BillingEvent:
        signature = {k.lower(): v for k, v in headers.items()}.get("stripe-signature", "")
        try:
            event = self.client().construct_event(raw_body, signature, self.webhook_secret())
        except (stripe.SignatureVerificationError, ValueError) as e:
            raise InvalidRequest("Invalid webhook signature") from e

        ignored = BillingEvent(event_id=event.id, event_type=f"stripe:{event.type}", tenant_id="",
                               plan=None, occurred_at=event.created)
        if event.type not in EVENTS:
            return ignored
        with stripe_errors("read the subscription"):
            current = self.client().v1.subscriptions.retrieve(event.data.object["id"])
        tenant_id = (current.metadata.to_dict() if current.metadata else {}).get("tenant_id")  # not a dict
        event_type = EVENT_FOR_STATUS.get(current.status)
        if not tenant_id or not event_type:
            logger.info("Stripe subscription ignored", extra={"subscription": current.id, "status": current.status,
                                                              "has_tenant": bool(tenant_id)})
            return ignored
        price = current["items"]["data"][0]["price"]["id"]
        return BillingEvent(event_id=event.id, event_type=event_type, tenant_id=tenant_id,
                            plan=self.config().plan_for_price.get(price), occurred_at=event.created,
                            customer_id=current.customer, subscription_id=current.id)


@contextmanager
def stripe_errors(action: str):
    """Turns a failed Stripe call into a 503 (clients and Stripe's webhooks retry), and logs why."""
    try:
        yield
    except stripe.StripeError as e:
        logger.error(f"Stripe: couldn't {action}", extra={"error": str(e), "code": e.code})
        raise ServiceBusy(f"The payment provider couldn't {action}. Try again in a moment.") from e
