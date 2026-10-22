"""
POST /webhooks/payments: called by the payment provider, not a logged-in user.
No login on this route; security comes from the signature check.
"""
import base64

from aws_lambda_powertools import Logger

from shared.errors import AppError

from billing.container import get_billing_service
from billing.service import BillingService

logger = Logger()


def process_webhook(event: dict, service: BillingService) -> dict:
    raw_body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode()
    try:
        service.handle_webhook(event.get("headers") or {}, raw_body)
    except AppError as e:
        logger.warning("Webhook rejected", extra={"reason": e.message})
        return {"statusCode": e.status_code, "body": e.message}
    # Any other error -> 500 -> the provider retries later.
    return {"statusCode": 200, "body": "ok"}


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return process_webhook(event, get_billing_service())
