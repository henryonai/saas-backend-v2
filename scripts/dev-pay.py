"""
Finishes a checkout on the development payment provider: sends the signed
webhook a real provider would send after a payment. DevPaymentProvider
(modules/billing/src/billing/provider.py) skips the checkout page but checks
webhook signatures for real, so the browser can't do this step itself. The
app's /dev-checkout page shows the command to run.

    uv run python scripts/dev-pay.py --config-env dev --tenant <tenant_id> --plan pro
    uv run python scripts/dev-pay.py --config-env dev --tenant <tenant_id> --event payment.failed
    uv run python scripts/dev-pay.py --config-env dev --tenant <tenant_id> --event subscription.cancelled

The webhook URL comes from the stack's outputs and the secret from SSM (the
names are in samconfig.toml). Only for stacks still on DevPaymentProvider.
"""
import argparse
import hashlib
import hmac
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import boto3

from shared.plans import PLANS

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ["subscription.activated", "subscription.cancelled", "payment.failed"]


def stack_settings(config_env: str) -> tuple[str, str, str]:
    """The stack name, region and payment secret's SSM name for this environment."""
    config = tomllib.loads((ROOT / "samconfig.toml").read_text())
    region = config["default"]["global"]["parameters"]["region"]
    deploy = config[config_env]["deploy"]["parameters"]
    overrides = dict(item.split("=", 1) for item in deploy["parameter_overrides"])
    return deploy["stack_name"], region, overrides["PaymentWebhookSecretParam"]


def webhook_url(stack: str, region: str) -> str:
    stack_info = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=stack)["Stacks"][0]
    return next(o["OutputValue"] for o in stack_info["Outputs"] if o["OutputKey"] == "WebhookUrl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-env", default="dev", help="dev or prod (samconfig.toml)")
    parser.add_argument("--tenant", required=True, help="the workspace's tenant_id")
    parser.add_argument("--plan", choices=sorted(PLANS), help="the plan paid for (subscription.activated)")
    parser.add_argument("--event", choices=EVENTS, default="subscription.activated")
    args = parser.parse_args()
    if args.event == "subscription.activated" and not args.plan:
        parser.error("--plan is required for subscription.activated")

    stack, region, secret_name = stack_settings(args.config_env)
    secret = boto3.client("ssm", region_name=region).get_parameter(
        Name=secret_name, WithDecryption=True)["Parameter"]["Value"]

    # The fields DevPaymentProvider.parse_webhook reads. A new id and time for
    # each call, so billing never mistakes it for a repeat or an older event.
    body = json.dumps({"id": f"evt_dev_{uuid4().hex}", "type": args.event, "tenant_id": args.tenant,
                       "plan": args.plan, "created": int(time.time())})
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    request = urllib.request.Request(webhook_url(stack, region), data=body.encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Signature": signature})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"{response.status} {response.read().decode()}: sent {args.event} for {args.tenant}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{e.code} {e.read().decode()}: the webhook was refused")


if __name__ == "__main__":
    main()
