from typing import Literal

from pydantic import SecretStr

from shared.settings import BaseAppSettings


class BillingSettings(BaseAppSettings):
    billing_table: str
    app_url: str = "http://localhost:5173"

    # "dev": the stand-in (checkout on the app's /dev-checkout page, webhooks
    # from scripts/dev-pay.py). "stripe": Stripe (stripe_provider.py).
    payment_provider: Literal["dev", "stripe"] = "dev"

    # The webhook secret (only the webhook function needs it). In AWS, set
    # PAYMENT_WEBHOOK_SECRET_PARAM to the name of an SSM SecureString parameter
    # (the secret itself is never in env vars). Locally, you can set
    # PAYMENT_WEBHOOK_SECRET directly in .env instead.
    payment_webhook_secret_param: str | None = None
    payment_webhook_secret: SecretStr | None = None  # SecretStr: hidden in logs

    # Stripe, the same way: in AWS the names of an SSM SecureString (the secret
    # key) and an SSM String (prices and portal, written by
    # scripts/stripe-setup.py); locally the values themselves.
    stripe_secret_key_param: str | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_config_param: str | None = None
    stripe_config: str | None = None
