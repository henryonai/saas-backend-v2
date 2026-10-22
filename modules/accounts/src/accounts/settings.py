from shared.settings import BaseAppSettings


class AccountsSettings(BaseAppSettings):
    accounts_table: str
    # Only needed by the API function (for invites). The login trigger
    # doesn't get it, because the user pool can't reference itself.
    user_pool_id: str | None = None
