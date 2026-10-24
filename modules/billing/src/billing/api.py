from typing import Annotated

from aws_lambda_powertools.event_handler import Depends
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.auth import CurrentUser, get_current_user

from billing.container import get_billing_service
from billing.schemas import BillingSummary, CheckoutRequest, CheckoutResponse, PortalResponse
from billing.service import BillingService

router = Router()

User = Annotated[CurrentUser, Depends(get_current_user)]
Service = Annotated[BillingService, Depends(get_billing_service)]


@router.get("/billing")
def get_billing(user: User, service: Service) -> BillingSummary:
    return BillingSummary.of(service.get_subscription(user))


# These return a URL; the frontend sends the browser there.
@router.post("/billing/checkout")
def checkout(body: CheckoutRequest, user: User, service: Service) -> CheckoutResponse:
    return CheckoutResponse(checkout_url=service.start_checkout(user, body.plan))


@router.post("/billing/portal")
def portal(user: User, service: Service) -> PortalResponse:
    return PortalResponse(portal_url=service.open_portal(user))
