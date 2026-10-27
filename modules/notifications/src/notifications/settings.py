from shared.settings import BaseAppSettings


class NotificationsSettings(BaseAppSettings):
    notifications_table: str
    # Verified SES sender. Empty = emails are only logged (local dev).
    email_sender: str = ""
    # Where invited people sign in or sign up.
    app_url: str = "http://localhost:5173"
    exports_bucket: str | None = None   # the worker writes notifications' part of a user's export there
