"""
Turns events about a workspace into audit entries, and lists them for its
owners and admins. Events arrive at least once and not in order: an entry is
keyed by the event's own time and id, so a repeat is skipped and the list is
in the order things happened.
"""
import time

from shared.auth import ROLE_ADMIN, ROLE_OWNER, CurrentUser
from shared.errors import InvalidRequest
from shared.exports import PartWriter

from audit.models import AuditEntry
from audit.repository import AuditRepository, InvalidCursor

# Events about a workspace, by name (modules never import each other). Add one
# here and to AuditRule in template.yaml to log it.
AUDITED = {"TenantCreated", "UserInvited", "InvitationCancelled", "MemberJoined", "RoleChanged", "MemberRemoved",
           "TenantDeletionScheduled", "TenantRestored", "SubscriptionChanged", "PaymentFailed", "OrderCreated",
           "ExportRequested"}
# Who did it, for events that don't say actor_id.
ACTOR_FIELD = {"OrderCreated": "user_id", "ExportRequested": "user_id"}
NOT_DETAILS = {"tenant_id", "event_id", "occurred_at", "actor_id", "actor_email", "owner_id", "owner_email"}


class AuditService:
    def __init__(self, entries: AuditRepository, retention_days: int, exports: PartWriter):
        self.entries = entries
        self.retention_days = retention_days
        self.exports = exports

    def list(self, user: CurrentUser, limit: int, cursor: str | None) -> tuple[list[AuditEntry], str | None]:
        """Owners and admins: who did what in the workspace, newest first."""
        tenant_id = user.require_role(ROLE_OWNER, ROLE_ADMIN)
        try:
            return self.entries.page(tenant_id, limit, cursor)
        except InvalidCursor:
            raise InvalidRequest("Invalid cursor") from None

    def handle_event(self, event_type: str, d: dict) -> None:
        if event_type == "TenantDeleted":
            self.entries.delete_tenant(d["tenant_id"])
        elif event_type == "ExportRequested" and d["kind"] == "workspace":
            self.exports.write(d, "audit", [e.model_dump() for e in self.entries.all(d["owner_id"])])
            self._log(event_type, d)
        elif event_type in AUDITED and d.get("tenant_id"):
            self._log(event_type, d)

    def _log(self, event_type: str, d: dict) -> None:
        entry = AuditEntry(event_id=d["event_id"], tenant_id=d["tenant_id"], type=event_type,
                           occurred_at=d["occurred_at"],
                           actor_id=d.get("actor_id") or d.get(ACTOR_FIELD.get(event_type, ""), ""),
                           actor_email=d.get("actor_email", ""),
                           details={k: v for k, v in d.items() if k not in NOT_DETAILS})
        self.entries.add(entry, int(time.time()) + self.retention_days * 24 * 3600)
