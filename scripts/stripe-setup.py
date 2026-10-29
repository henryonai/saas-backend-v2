"""
Sets Stripe up for a deployed stack.
Run it after the stack's first deploy, and again after changing the plans in
layers/shared/layer/shared/plans.py:

    uv run python scripts/stripe-setup.py --stack saas-dev
    uv run python scripts/stripe-setup.py --stack saas-dev --new-webhook-secret

It reads the Stripe secret key from SSM (the stack's StripeSecretKeyParam; the
key decides test or live mode), then creates what is missing and updates the
rest, so running it again duplicates nothing:

  1. a product and a monthly USD price for each paid plan. A changed price
     becomes a new Stripe price; subscribers keep the one they have.
  2. the customer portal: card, invoices, plan changes (prorated), and
     cancelling at the end of the period.
  3. the webhook endpoint for the stack's WebhookUrl, with the events billing
     handles. Its signing secret goes to SSM (PaymentWebhookSecretParam).
  4. the prices and the portal, saved in SSM (StripeConfigParam).

The functions read the new values within 5 minutes; no deploy needed.
Secrets are never printed.
"""
import argparse
import json
import sys
import tomllib
from pathlib import Path

import boto3
import stripe
from botocore.exceptions import ClientError

from billing.stripe_provider import EVENTS
from shared.plans import PLANS

ROOT = Path(__file__).resolve().parent.parent


def ssm_value(ssm, name: str) -> str | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def metadata(obj) -> dict:
    return obj.metadata.to_dict() if obj.metadata else {}   # Stripe objects aren't dicts


def ensure_price(client: stripe.StripeClient, stack: str, plan_id: str, plan: dict) -> tuple[str, str, list[str]]:
    """The plan's current price, its product, and every price that product ever had."""
    lookup_key = f"{stack}-{plan_id}-monthly"
    amount = plan["price_usd_month"] * 100
    found = client.v1.prices.list({"lookup_keys": [lookup_key], "limit": 1}).data
    old = found[0] if found else None
    if old and old.active and old.unit_amount == amount and old.currency == "usd":
        price = old
    else:
        product = old.product if old else client.v1.products.create(
            {"name": plan["name"], "metadata": {"stack": stack, "plan": plan_id}}).id
        price = client.v1.prices.create({
            "product": product, "currency": "usd", "unit_amount": amount, "recurring": {"interval": "month"},
            "lookup_key": lookup_key, "transfer_lookup_key": True, "metadata": {"stack": stack, "plan": plan_id},
        })
        if old:
            client.v1.prices.update(old.id, {"active": False})   # new checkouts use the new price
    client.v1.products.update(price.product, {"name": plan["name"]})
    every = [p.id for p in client.v1.prices.list({"product": price.product, "limit": 100}).auto_paging_iter()]
    return price.id, price.product, every


def ensure_portal(client: stripe.StripeClient, stack: str, app_url: str, products: list[tuple[str, str]]) -> str:
    params = {
        "features": {
            "customer_update": {"enabled": True, "allowed_updates": ["email", "name", "address"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            "subscription_update": {
                "enabled": True, "default_allowed_updates": ["price"], "proration_behavior": "always_invoice",
                "products": [{"product": product, "prices": [price]} for product, price in products],
            },
        },
        "default_return_url": f"{app_url}/billing",
        "metadata": {"stack": stack},
    }
    for config in client.v1.billing_portal.configurations.list({"limit": 100}).auto_paging_iter():
        if config.active and metadata(config).get("stack") == stack:
            return client.v1.billing_portal.configurations.update(config.id, params).id
    return client.v1.billing_portal.configurations.create(params).id


def ensure_webhook(client: stripe.StripeClient, ssm, stack: str, url: str, secret_param: str, new_secret: bool) -> str:
    """Creates the endpoint, or updates its events. A new endpoint's secret goes straight to SSM."""
    endpoints = [e for e in client.v1.webhook_endpoints.list({"limit": 100}).auto_paging_iter() if e.url == url]
    have_secret = (ssm_value(ssm, secret_param) or "").startswith("whsec_")
    if endpoints and have_secret and not new_secret:
        for extra in endpoints[1:]:
            client.v1.webhook_endpoints.delete(extra.id)
        client.v1.webhook_endpoints.update(endpoints[0].id, {"enabled_events": EVENTS, "disabled": False})
        return f"{endpoints[0].id} (updated)"
    for endpoint in endpoints:   # its secret isn't in SSM, and Stripe only shows it once: start over
        client.v1.webhook_endpoints.delete(endpoint.id)
    endpoint = client.v1.webhook_endpoints.create({
        "url": url, "enabled_events": EVENTS, "api_version": stripe.api_version,
        "description": f"{stack} billing", "metadata": {"stack": stack},
    })
    ssm.put_parameter(Name=secret_param, Value=endpoint.secret, Type="SecureString", Overwrite=True)
    return f"{endpoint.id} (new, signing secret saved to {secret_param})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stack", default="saas-dev")
    parser.add_argument("--new-webhook-secret", action="store_true",
                        help="recreate the webhook endpoint, which gives it a new signing secret")
    args = parser.parse_args()

    region = tomllib.loads((ROOT / "samconfig.toml").read_text())["default"]["global"]["parameters"]["region"]
    session = boto3.session.Session(region_name=region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    params = {p["ParameterKey"]: p.get("ParameterValue", "") for p in stack.get("Parameters", [])}
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    if "StripeSecretKeyParam" not in params:
        sys.exit(f"{args.stack} was deployed before Stripe support: deploy it again first.")
    ssm = session.client("ssm")

    key = ssm_value(ssm, params["StripeSecretKeyParam"])
    if not key:
        sys.exit(f"No Stripe key in SSM yet. Store it yourself (it isn't echoed):\n"
                 f"  read -rs STRIPE_KEY && aws ssm put-parameter --region {region} "
                 f"--name {params['StripeSecretKeyParam']} --type SecureString \\\n"
                 f"      --value \"$STRIPE_KEY\"; unset STRIPE_KEY")
    client = stripe.StripeClient(key)
    mode = "live" if key.startswith(("sk_live_", "rk_live_")) else "test"
    app_url = params.get("AppUrl") or outputs["WebUrl"]

    prices, plan_for_price, products = {}, {}, []
    for plan_id, plan in PLANS.items():
        if plan["price_usd_month"] > 0:
            price, product, every = ensure_price(client, args.stack, plan_id, plan)
            prices[plan_id] = price
            plan_for_price.update(dict.fromkeys(every, plan_id))
            products.append((product, price))
    portal = ensure_portal(client, args.stack, app_url, products)
    webhook = ensure_webhook(client, ssm, args.stack, outputs["WebhookUrl"], params["PaymentWebhookSecretParam"],
                             args.new_webhook_secret)
    config = {"prices": prices, "plan_for_price": plan_for_price, "portal_configuration": portal}
    ssm.put_parameter(Name=params["StripeConfigParam"], Value=json.dumps(config, sort_keys=True),
                      Type="String", Overwrite=True)

    print(f"Stripe ({mode} mode) for {args.stack}:")
    for plan_id, price in prices.items():
        print(f"  {plan_id:<9} {price}  ${PLANS[plan_id]['price_usd_month']}/month")
    print(f"  portal    {portal}")
    print(f"  webhook   {webhook}")
    print(f"  config    saved to {params['StripeConfigParam']}")
    print("The functions read it within 5 minutes.")


if __name__ == "__main__":
    main()
