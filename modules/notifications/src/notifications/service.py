"""
Turns events into notifications. To add one: write a handler method and add
it to HANDLERS, then add the event name to the rule in template.yaml.
"""
from shared.auth import CurrentUser
from shared.errors import NotFound

from notifications.email import EmailSender
from notifications.models import Notification
from notifications.repository import NotificationRepository


class NotificationService:
    def __init__(self, notifications: NotificationRepository, email: EmailSender):
        self.notifications = notifications
        self.email = email

    # ---------- API ----------

    def list_mine(self, user: CurrentUser, limit: int) -> list[Notification]:
        return self.notifications.list(user.require_tenant(), user.user_id, limit)

    def mark_read(self, user: CurrentUser, notification_id: str) -> None:
        if not self.notifications.mark_read(user.require_tenant(), user.user_id, notification_id):
            raise NotFound("Notification not found")

    # ---------- events ----------

    def handle_event(self, event_type: str, detail: dict) -> None:
        handler = self.HANDLERS.get(event_type)
        if handler:
            handler(self, detail)

    def _in_app(self, tenant_id: str, user_id: str, title: str, body: str) -> None:
        self.notifications.save(Notification(tenant_id=tenant_id, user_id=user_id,
                                             title=title, body=body))

    def _notify_owner(self, d: dict, title: str, body: str) -> None:
        if d.get("owner_id"):
            self._in_app(d["tenant_id"], d["owner_id"], title, body)
        if d.get("owner_email"):
            self.email.send(d["owner_email"], title, body)

    def _on_order_created(self, d: dict) -> None:
        self._in_app(d["tenant_id"], d["user_id"], "Order created",
                     f"Your order for {d['item']} was created.")

    def _on_user_invited(self, d: dict) -> None:
        self.email.send(
            d["email"], f"You've been invited to {d['tenant_name']}",
            f"{d['invited_by']} invited you to {d['tenant_name']}. "
            "Check your inbox for a separate email with your temporary password.",
        )

    def _on_subscription_changed(self, d: dict) -> None:
        if d["status"] not in ("active", "cancelled"):
            return  # e.g. past_due is covered by PaymentFailed
        title = f"Plan changed to {d['plan']}"
        self._notify_owner(d, title, f"Your workspace is now on the {d['plan']} plan.")

    def _on_payment_failed(self, d: dict) -> None:
        self._notify_owner(d, "Payment failed",
                           "We couldn't process your payment. Please update your payment method.")

    HANDLERS = {
        "OrderCreated": _on_order_created,
        "UserInvited": _on_user_invited,
        "SubscriptionChanged": _on_subscription_changed,
        "PaymentFailed": _on_payment_failed,
    }
