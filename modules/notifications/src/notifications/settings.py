from shared.settings import BaseAppSettings


class NotificationsSettings(BaseAppSettings):
    notifications_table: str
    # Verified SES sender. Empty = emails are only logged (local dev).
    email_sender: str = ""
