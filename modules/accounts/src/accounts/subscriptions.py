"""
Listens to SubscriptionChanged events from billing (EventBridge -> this Lambda)
and keeps the tenant's local copy of its plan up to date.
"""
from aws_lambda_powertools import Logger

from accounts.container import get_account_service
from accounts.service import AccountService

logger = Logger()


def on_subscription_changed(event: dict, service: AccountService) -> None:
    d = event["detail"]
    logger.info("Subscription changed", extra={"tenant_id": d["tenant_id"], "plan": d["plan"]})
    service.apply_subscription_change(d["tenant_id"], d["plan"], d["status"])


@logger.inject_lambda_context
def handler(event: dict, context) -> None:
    on_subscription_changed(event, get_account_service())
