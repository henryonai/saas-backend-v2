"""
What the billing module stores and works with. The repository saves these;
the service returns them. What the API accepts and returns is in schemas.py.
"""
from pydantic import BaseModel


class Subscription(BaseModel):
    tenant_id: str
    plan: str = "free"
    status: str = "active"  # active / past_due / cancelled
    # Copied from the token when the owner starts checkout, so billing events
    # can include owner details without asking the accounts module.
    owner_id: str = ""
    owner_email: str = ""
    # The provider's ids for this workspace (Stripe: cus_... and sub_...), saved
    # from its webhooks. Plan changes and the billing portal need them.
    customer_id: str = ""
    subscription_id: str = ""
    # Increases with every save; listeners use it to ignore late events.
    revision: int = 0
    # The provider event that last set each field, so older or repeated
    # webhooks don't overwrite newer information (see service.py).
    plan_changed_at: int = 0
    plan_event_id: str = ""
    status_changed_at: int = 0
    status_event_id: str = ""
    # When its workspace was purged (epoch seconds; 0: it wasn't). Then the record
    # stays a while, so the provider's late webhooks can't bring it back.
    deleted_at: int = 0
