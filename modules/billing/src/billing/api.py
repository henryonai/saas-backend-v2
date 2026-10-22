from typing import Annotated

from aws_lambda_powertools.event_handler import Depends
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.auth import CurrentUser, get_current_user

from billing.container import get_billing_service
from billing.schemas import BillingSummary, CheckoutRequest, CheckoutResponse
from billing.service import BillingService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[BillingService, Depends(get_billing_service)]


@router.get("/billing")
def get_billing(user: User, service: Service) -> BillingSummary:
    return BillingSummary.of(service.get_subscription(user))


# Returns a URL; the frontend redirects the browser to it.
@router.post("/billing/checkout")
def checkout(body: CheckoutRequest, user: User, service: Service) -> CheckoutResponse:
    return CheckoutResponse(checkout_url=service.start_checkout(user, body.plan))
