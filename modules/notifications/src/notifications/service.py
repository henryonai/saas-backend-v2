"""
Turns events into notifications. To add one: write a handler method and add
it to HANDLERS, then add the event name to the rule in template.yaml.

Events can be delivered more than once. In-app notifications get an id
derived from the event, so a repeat is skipped; the worker also records
processed events so emails aren't sent twice.
"""
from shared.auth import CurrentUser
from shared.errors import NotFound
from shared.ids import sortable_id

from notifications.email import EmailSender
from notifications.models import Notification
from notifications.repository import NotificationRepository


class NotificationService:
    def __init__(self, notifications: NotificationRepository, email: EmailSender, app_url: str):
        self.notifications = notifications
        self.email = email
        self.app_url = app_url

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

    def _in_app(self, d: dict, user_id: str, title: str, body: str) -> None:
        # Same event -> same id, so a repeated delivery doesn't add a second one.
        notification_id = sortable_id(at=d["occurred_at"], unique=d["event_id"])
        self.notifications.save(Notification(notification_id=notification_id, tenant_id=d["tenant_id"],
                                             user_id=user_id, title=title, body=body))

    def _notify_owner(self, d: dict, title: str, body: str) -> None:
        if d.get("owner_id"):
            self._in_app(d, d["owner_id"], title, body)
        if d.get("owner_email"):
            self.email.send(d["owner_email"], title, body)

    def _on_order_created(self, d: dict) -> None:
        self._in_app(d, d["user_id"], "Order created", f"Your order for {d['item']} was created.")

    def _on_user_invited(self, d: dict) -> None:
        # The workspace name is typed by whoever invites, so it is quoted and the
        # email says nothing happens unless the person accepts.
        self.email.send(
            d["email"], "You've been invited to a workspace",
            f"{d['invited_by']} invited you to join the workspace \"{d['tenant_name']}\".\n\n"
            f"To accept or decline, sign in or create an account with this email address at "
            f"{self.app_url}. If you don't know this person, ignore this email: nothing "
            f"happens unless you accept.",
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
