from pydantic import SecretStr, model_validator

from shared.settings import BaseAppSettings


class BillingSettings(BaseAppSettings):
    billing_table: str
    app_url: str = "http://localhost:5173"

    # The webhook secret. In AWS, set PAYMENT_WEBHOOK_SECRET_PARAM to the name
    # of an SSM SecureString parameter (the secret itself is never in env vars).
    # Locally, you can set PAYMENT_WEBHOOK_SECRET directly in .env instead.
    payment_webhook_secret_param: str | None = None
    payment_webhook_secret: SecretStr | None = None  # SecretStr: hidden in logs

    @model_validator(mode="after")
    def secret_source_required(self):
        if not (self.payment_webhook_secret_param or self.payment_webhook_secret):
            raise ValueError(
                "Set PAYMENT_WEBHOOK_SECRET_PARAM (AWS) or PAYMENT_WEBHOOK_SECRET (local)"
            )
        return self
