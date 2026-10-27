"""
The back-office: platform admins list every workspace and act on one. The
list is a read model this module keeps from other modules' events, so it
never reads their tables. Acting publishes an event; the module that owns the
data applies it (billing the plan, accounts the suspension), and the change
comes back here as events too.
"""
from shared.auth import CurrentUser
from shared.errors import Conflict, InvalidRequest, NotFound
from shared.plans import is_plan

from admin import events
from admin.models import AdminAction, WorkspaceSummary
from admin.repository import TENANTS, AdminRepository, InvalidCursor


def _key(tenant_id: str) -> dict:
    return {"pk": TENANTS, "sk": tenant_id}


def _set(tenant_id: str, fields: dict, add: dict | None = None, condition: str | None = None,
         values: dict | None = None) -> dict:
    """An Update of the workspace's summary: SET these fields, ADD these counters."""
    names = {f"#{k}": k for k in fields} | {f"#{k}": k for k in add or {}}
    expression = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    if add:
        expression += " ADD " + ", ".join(f"#{k} :{k}" for k in add)
    update = {"Key": _key(tenant_id), "UpdateExpression": expression, "ExpressionAttributeNames": names,
              "ExpressionAttributeValues": {f":{k}": v for k, v in (fields | (add or {})).items()} | (values or {})}
    if condition:
        update["ConditionExpression"] = condition
    return {"Update": update}


class AdminService:
    def __init__(self, admin: AdminRepository):
        self.admin = admin

    # ---------- the read model, from events ----------

    def handle_event(self, event_type: str, d: dict) -> None:
        t = d.get("tenant_id")
        if not t:
            return
        update = None
        if event_type == "TenantCreated":
            update = _set(t, {"tenant_id": t, "name": d["name"], "owner_id": d["actor_id"],
                              "created_at": d["occurred_at"]}, add={"members": 1})
        elif event_type == "MemberJoined":
            update = _set(t, {"tenant_id": t}, add={"members": 1})
        elif event_type == "MemberRemoved":
            update = _set(t, {"tenant_id": t}, add={"members": -1})
        elif event_type == "OrderCreated":
            update = _set(t, {"tenant_id": t}, add={"orders": 1})
        elif event_type == "SubscriptionChanged":   # only a newer revision changes the plan
            update = _set(t, {"tenant_id": t, "plan": d["plan"], "subscription_status": d["status"],
                              "revision": d["revision"], "comped": bool(d.get("comped"))},
                          condition="attribute_not_exists(#revision) OR #revision < :revision")
        elif event_type in ("TenantDeletionScheduled", "TenantRestored", "TenantSuspended", "TenantUnsuspended"):
            status = {"TenantDeletionScheduled": "deleting", "TenantSuspended": "suspended"}.get(event_type, "active")
            update = _set(t, {"tenant_id": t, "status": status})
        elif event_type == "TenantDeleted":
            update = {"Delete": {"Key": _key(t)}}
        self.admin.apply(d["event_id"], update)

    # ---------- what platform admins see and do ----------

    def list_workspaces(self, admin: CurrentUser, limit: int, cursor: str | None):
        admin.require_platform_admin()
        try:
            return self.admin.page(limit, cursor)
        except InvalidCursor:
            raise InvalidRequest("Invalid cursor") from None

    def get_workspace(self, admin: CurrentUser, tenant_id: str) -> WorkspaceSummary:
        admin.require_platform_admin()
        return self._workspace(tenant_id)

    def grant_plan(self, admin: CurrentUser, tenant_id: str, plan: str, note: str) -> None:
        """Billing gives the workspace this plan without a payment, until the provider changes it."""
        admin.require_platform_admin()
        if not is_plan(plan):
            raise InvalidRequest(f"Unknown plan {plan!r}")
        self._workspace(tenant_id)
        self.admin.act(self._action(admin, "grant_plan", tenant_id, {"plan": plan, "note": note}),
                       events.plan_granted(tenant_id, plan, note, admin))

    def suspend(self, admin: CurrentUser, tenant_id: str, reason: str) -> None:
        """Nobody can work in it until it is unsuspended: accounts takes it out of every token."""
        admin.require_platform_admin()
        if self._workspace(tenant_id).status == "suspended":
            raise Conflict("This workspace is suspended already")
        self.admin.act(self._action(admin, "suspend", tenant_id, {"reason": reason}),
                       events.tenant_suspended(tenant_id, reason, admin))

    def unsuspend(self, admin: CurrentUser, tenant_id: str) -> None:
        admin.require_platform_admin()
        if self._workspace(tenant_id).status != "suspended":
            raise Conflict("This workspace isn't suspended")
        self.admin.act(self._action(admin, "unsuspend", tenant_id), events.tenant_unsuspended(tenant_id, admin))

    def log(self, admin: CurrentUser, limit: int, cursor: str | None):
        admin.require_platform_admin()
        try:
            return self.admin.log(limit, cursor)
        except InvalidCursor:
            raise InvalidRequest("Invalid cursor") from None

    def _workspace(self, tenant_id: str) -> WorkspaceSummary:
        summary = self.admin.get(tenant_id)
        if not summary:
            raise NotFound("Workspace not found")
        return summary

    @staticmethod
    def _action(admin: CurrentUser, action: str, tenant_id: str, details: dict | None = None) -> AdminAction:
        return AdminAction(admin_id=admin.user_id, admin_email=admin.email.lower(), action=action,
                           tenant_id=tenant_id, details=details or {})
