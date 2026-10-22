from shared.settings import BaseAppSettings


class AccountsSettings(BaseAppSettings):
    accounts_table: str
    # Only needed by the API function (to sign users out). The login trigger
    # doesn't get it, because the user pool can't reference itself.
    user_pool_id: str | None = None
    # Invitation rules (see InvitationLimits in service.py).
    invitation_days: int = 7          # how long an invitation can be accepted
    invites_per_day: int = 20         # per workspace (anti-spam)
    max_pending_invitations_per_email: int = 5  # open invitations one person can have (anti-spam)
