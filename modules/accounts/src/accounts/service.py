"""
Accounts business rules. Every dependency is passed in (no defaults, no
globals), so you can see exactly what this service needs.

Roles are checked against this module's table, not the token: a token keeps
its claims until it expires, and a removed or demoted admin must lose access
to these actions immediately.

Invitations are only joined when the invited person accepts one, signed in
with that (verified) email. Nobody is added to a workspace without saying yes.

A user can belong to several workspaces (up to max_workspaces). The login
token lists them all, and each request names the one it acts in (shared/auth.py).
"""
import json
import time
from dataclasses import dataclass

from shared.auth import ROLE_ADMIN, ROLE_OWNER, WORKSPACES_CLAIM, CurrentUser
from shared.dynamo import ConditionFailed
from shared.errors import Conflict, Forbidden, NotFound, PlanLimitReached, TooManyRequests
from shared.idempotency import IDEMPOTENCY, fingerprint
from shared.plans import get_limit

from accounts import events
from accounts.identity import IdentityProvider
from accounts.models import Invitation, Member, Tenant
from accounts.repository import AccountRepository
from accounts.schemas import InviteRequest, TenantCreate

@dataclass(frozen=True)
class InvitationLimits:
    """Invitation rules, read from settings by the container."""
    days: int                # how long an invitation can be accepted
    per_day: int             # invitations a workspace can send per day
    pending_per_email: int   # open invitations one email can have


class AccountService:
    def __init__(self, accounts: AccountRepository, identity: IdentityProvider,
                 invitation_limits: InvitationLimits, max_workspaces: int):
        self.accounts = accounts
        self.identity = identity
        self.limits = invitation_limits
        self.max_workspaces = max_workspaces   # workspaces one user can belong to

    # ---------- tenants ----------

    def create_tenant(self, user: CurrentUser, data: TenantCreate, idempotency_key: str) -> Tenant:
        """
        The creator becomes the owner. Their next token lists the new workspace.
        A retry with the same Idempotency-Key (a timeout, or opening it failed)
        returns the workspace the first request made instead of making another.
        """
        tenant = Tenant(name=data.name, owner_id=user.user_id)
        owner = Member(tenant_id=tenant.tenant_id, user_id=user.user_id,
                       email=user.email.lower(), role=ROLE_OWNER)
        request = fingerprint(data.model_dump(mode="json"))
        try:
            self.accounts.create_tenant(tenant, owner, self.max_workspaces, idempotency=(idempotency_key, request))
        except ConditionFailed as e:
            if IDEMPOTENCY in e.failed:   # before the limit: the workspace it made is counted already
                created = self.accounts.tenant_for_key(user.user_id, idempotency_key, request)
                return self._tenant(created, consistent=True)
            raise self._too_many_workspaces()
        return tenant

    def get_tenant(self, user: CurrentUser) -> Tenant:
        return self._tenant(self._caller(user).tenant_id)

    def my_workspaces(self, user: CurrentUser) -> list[tuple[Tenant, Member]]:
        """Every workspace the caller belongs to, by name, as stored now."""
        return sorted(self.accounts.workspaces_of(user.user_id), key=lambda w: w[0].name.lower())

    # ---------- members ----------

    def list_members(self, user: CurrentUser) -> list[Member]:
        return self.accounts.list_members(self._caller(user).tenant_id)

    def change_role(self, user: CurrentUser, target_user_id: str, role: str) -> Member:
        tenant_id = self._caller(user, ROLE_OWNER, ROLE_ADMIN).tenant_id
        target = self._get_member(tenant_id, target_user_id)
        if target.role == ROLE_OWNER:
            raise Forbidden("The owner's role can't be changed")
        if not self.accounts.change_role(tenant_id, target_user_id, role):
            raise NotFound("User not found in this workspace")
        self.identity.sign_out(target_user_id)  # their next token carries the new role
        return target.model_copy(update={"role": role})

    def remove(self, user: CurrentUser, target_user_id: str) -> None:
        """Owners and admins remove others; any member except the owner can remove (leave) themselves."""
        leaving = target_user_id == user.user_id
        caller = self._caller(user) if leaving else self._caller(user, ROLE_OWNER, ROLE_ADMIN)
        target = caller if leaving else self._get_member(caller.tenant_id, target_user_id)
        if target.role == ROLE_OWNER:
            raise Forbidden("The owner can't leave or be removed")
        if not leaving:
            # Removed by someone else: their tokens go now, not in up to 15 minutes, and
            # sign them out of every workspace. Sign out first: if removing then fails, they
            # simply log in again. Someone who leaves keeps their session: the app refreshes
            # the token, which then no longer lists this workspace.
            self.identity.sign_out(target_user_id)
        removed = events.member_removed(caller.tenant_id, target_user_id, removed_by=user.user_id)
        if not self.accounts.remove_member(caller.tenant_id, target_user_id, removed):
            raise NotFound("User not found in this workspace")

    # ---------- invitations sent by a workspace ----------

    def invite(self, user: CurrentUser, data: InviteRequest) -> Invitation:
        tenant_id = self._caller(user, ROLE_OWNER, ROLE_ADMIN).tenant_id
        if not user.email_verified:
            raise Forbidden("Verify your email address before inviting people")
        tenant = self._tenant(tenant_id)

        email = data.email.lower()
        members = self.accounts.list_members(tenant_id)
        if any(m.email.lower() == email for m in members):
            raise Conflict(f"{email} is already in this workspace")

        # Seats are only taken when an invitation is accepted (checked again
        # then, atomically). This check just stops inviting more people than fit.
        max_users = get_limit(tenant.plan, "max_users")
        pending = self._pending(self.accounts.list_invitations(tenant_id))
        if max_users is not None and len(members) + len(pending) >= max_users:
            raise PlanLimitReached(f"Your {tenant.plan} plan allows {max_users} users. Upgrade to invite more.")
        if len(self._pending(self.accounts.invitations_for(email))) >= self.limits.pending_per_email:
            raise TooManyRequests(f"{email} has too many pending invitations")

        invitation = Invitation(tenant_id=tenant_id, tenant_name=tenant.name, email=email,
                                role=data.role, invited_by=user.email,
                                expires_at=int(time.time()) + self.limits.days * 24 * 3600)
        try:
            self.accounts.invite(invitation, events.user_invited(invitation), self.limits.per_day)
        except ConditionFailed as e:
            if "invitation" in e.failed:
                raise Conflict(f"{email} is already invited")
            raise TooManyRequests(f"You can send {self.limits.per_day} invitations a day. Try again tomorrow.")
        return invitation

    def list_invitations(self, user: CurrentUser) -> list[Invitation]:
        return self._pending(self.accounts.list_invitations(self._caller(user).tenant_id))

    def cancel_invitation(self, user: CurrentUser, email: str) -> None:
        tenant_id = self._caller(user, ROLE_OWNER, ROLE_ADMIN).tenant_id
        if not self.accounts.delete_invitation(tenant_id, email.lower()):
            raise NotFound("Invitation not found")

    # ---------- invitations received by the signed-in user ----------

    def my_invitations(self, user: CurrentUser) -> list[Invitation]:
        return self._pending(self.accounts.invitations_for(self._verified_email(user)))

    def accept_invitation(self, user: CurrentUser, tenant_id: str) -> Tenant:
        """Joins the workspace, keeping any others. The frontend must then refresh the token."""
        email = self._verified_email(user)
        invitation = self.accounts.get_invitation(tenant_id, email)
        if not invitation or invitation.expired():
            raise NotFound("Invitation not found or expired")
        tenant = self._tenant(tenant_id)
        member = Member(tenant_id=tenant_id, user_id=user.user_id, email=email, role=invitation.role)
        try:
            self.accounts.accept_invitation(invitation, member, get_limit(tenant.plan, "max_users"),
                                            self.max_workspaces)
        except ConditionFailed as e:
            if "membership" in e.failed:
                raise Conflict("You are already in this workspace")
            if "workspaces" in e.failed:
                raise self._too_many_workspaces()
            if "invitation" in e.failed:
                raise NotFound("Invitation not found or expired")
            raise PlanLimitReached("This workspace is full. Ask its owner to upgrade the plan.")
        return tenant

    def decline_invitation(self, user: CurrentUser, tenant_id: str) -> None:
        if not self.accounts.delete_invitation(tenant_id, self._verified_email(user)):
            raise NotFound("Invitation not found")

    # ---------- used by the login trigger and event listener ----------

    def token_claims_for(self, user_id: str) -> dict:
        """
        Claims to put in the login token: every workspace the user belongs to,
        with their role there and its plan (shared/auth.py reads them). Empty if
        the user has no workspace yet.
        """
        workspaces = {tenant.tenant_id: {"role": member.role, "plan": tenant.plan}
                      for tenant, member in self.accounts.workspaces_of(user_id)}
        return {WORKSPACES_CLAIM: json.dumps(workspaces, separators=(",", ":"))} if workspaces else {}

    def apply_subscription_change(self, tenant_id: str, plan: str, status: str, revision: int) -> None:
        """Called when billing publishes SubscriptionChanged. Late or repeated events are skipped."""
        self.accounts.apply_subscription(tenant_id, plan, status, revision)

    # ---------- helpers ----------

    def _too_many_workspaces(self) -> Conflict:
        return Conflict(f"You can belong to at most {self.max_workspaces} workspaces. Leave one first.")

    def _caller(self, user: CurrentUser, *roles: str) -> Member:
        """The caller's membership as stored now (the token may be minutes old)."""
        member = self.accounts.get_member(user.require_tenant(), user.user_id)
        if not member:
            raise Forbidden("You are not a member of this workspace")
        if roles and member.role not in roles:
            raise Forbidden(f"This action needs one of these roles: {', '.join(roles)}")
        return member

    @staticmethod
    def _verified_email(user: CurrentUser) -> str:
        if not user.email_verified:
            raise Forbidden("Verify your email address first")
        return user.email.lower()

    @staticmethod
    def _pending(invitations: list[Invitation]) -> list[Invitation]:
        return [i for i in invitations if not i.expired()]

    def _tenant(self, tenant_id: str, consistent: bool = False) -> Tenant:
        tenant = self.accounts.get_tenant(tenant_id, consistent)
        if not tenant:
            raise NotFound("Workspace not found")
        return tenant

    def _get_member(self, tenant_id: str, user_id: str) -> Member:
        member = self.accounts.get_member(tenant_id, user_id)
        if not member:
            raise NotFound("User not found in this workspace")
        return member
