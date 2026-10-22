"""
Accounts business rules. Every dependency is passed in (no defaults, no
globals), so you can see exactly what this service needs.
"""
from shared.auth import ROLE_ADMIN, ROLE_OWNER, CurrentUser
from shared.errors import Conflict, Forbidden, NotFound, PlanLimitReached
from shared.events import EventPublisher
from shared.plans import get_limit

from accounts.identity import IdentityProvider
from accounts.models import InviteRequest, Member, Tenant, TenantCreate
from accounts.repository import MemberRepository, TenantRepository


class AccountService:
    def __init__(self, tenants: TenantRepository, members: MemberRepository,
                 identity: IdentityProvider, publisher: EventPublisher):
        self.tenants = tenants
        self.members = members
        self.identity = identity
        self.publisher = publisher

    # ---------- tenants ----------

    def create_tenant(self, user: CurrentUser, data: TenantCreate) -> Tenant:
        """First step after sign-up. The creator becomes the owner."""
        if self.members.find_by_user(user.user_id):
            raise Conflict("You already belong to a workspace")

        tenant = Tenant(name=data.name, owner_id=user.user_id)
        self.tenants.save(tenant)
        self.members.save(Member(tenant_id=tenant.tenant_id, user_id=user.user_id,
                                 email=user.email, role=ROLE_OWNER))
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            raise NotFound("Workspace not found")
        return tenant

    # ---------- members ----------

    def list_members(self, user: CurrentUser) -> list[Member]:
        return self.members.list(user.require_tenant())

    def invite(self, user: CurrentUser, data: InviteRequest) -> Member:
        tenant_id = user.require_role(ROLE_OWNER, ROLE_ADMIN)
        tenant = self.get_tenant(tenant_id)
        existing = self.members.list(tenant_id)

        email = data.email.lower()
        if any(m.email.lower() == email for m in existing):
            raise Conflict(f"{email} is already in this workspace")

        # Use our own copy of the plan (fresh even before the token refreshes).
        max_users = get_limit(tenant.plan, "max_users")
        if max_users is not None and len(existing) >= max_users:
            raise PlanLimitReached(
                f"Your {tenant.plan} plan allows {max_users} users. Upgrade to invite more."
            )

        member = Member(tenant_id=tenant_id, user_id=self.identity.create_user(email),
                        email=email, role=data.role, status="invited")
        self.members.save(member)

        # Fat event: notifications gets everything it needs to send the email.
        self.publisher.publish("UserInvited", tenant_id, user_id=member.user_id,
                               email=email, invited_by=user.email, tenant_name=tenant.name)
        return member

    def change_role(self, user: CurrentUser, target_user_id: str, role: str) -> Member:
        tenant_id = user.require_role(ROLE_OWNER, ROLE_ADMIN)
        target = self._get_member(tenant_id, target_user_id)
        if target.role == ROLE_OWNER:
            raise Forbidden("The owner's role can't be changed")

        self.members.update_role(tenant_id, target_user_id, role)
        return target.model_copy(update={"role": role})

    def remove(self, user: CurrentUser, target_user_id: str) -> None:
        tenant_id = user.require_role(ROLE_OWNER, ROLE_ADMIN)
        target = self._get_member(tenant_id, target_user_id)
        if target.role == ROLE_OWNER:
            raise Forbidden("The owner can't be removed")
        if target_user_id == user.user_id:
            raise Forbidden("You can't remove yourself")
        self.members.delete(tenant_id, target_user_id)

    # ---------- used by the login trigger and event listener ----------

    def token_claims_for(self, user_id: str) -> dict:
        """Claims to put in the login token. Empty if the user has no workspace yet."""
        member = self.members.find_by_user(user_id)
        if not member:
            return {}
        tenant = self.tenants.get(member.tenant_id)
        return {
            "tenant_id": member.tenant_id,
            "role": member.role,
            "plan": tenant.plan if tenant else "free",
        }

    def apply_subscription_change(self, tenant_id: str, plan: str, status: str) -> None:
        """Called when billing publishes SubscriptionChanged."""
        if self.tenants.get(tenant_id):
            self.tenants.update_subscription(tenant_id, plan, status)

    def _get_member(self, tenant_id: str, user_id: str) -> Member:
        member = self.members.get(tenant_id, user_id)
        if not member:
            raise NotFound("User not found in this workspace")
        return member
