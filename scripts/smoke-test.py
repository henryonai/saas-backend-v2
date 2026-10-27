# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3", "pycognito", "requests", "stripe>=16"]
# ///
"""
Smoke test for a deployed stack, run after sam deploy. Creates three test
users and calls every endpoint, printing PASS/FAIL per check:

  auth, workspaces, orders (status rules, paging), billing (checkout, signed
  webhooks, out-of-order and repeated webhooks, payment failed, cancel),
  events between modules (outbox -> EventBridge -> accounts and notifications
  workers), invitations (send, cancel, accept, decline), roles and who may do
  what, leaving and removing members, tenant isolation, users in several
  workspaces (X-Tenant-Id), plan limits, and deleting, restoring and (with
  TenantDeletionDays=0, as on dev) purging a workspace.

    uv run scripts/smoke-test.py [--stack saas-dev]

On a stack with PaymentProvider=stripe (test mode), billing goes through
Stripe instead: it subscribes with Stripe's test cards through Stripe's API,
and Stripe's own webhooks change the plan. It needs scripts/stripe-setup.py
to have run for the stack.

Uses your AWS credentials (AWS_PROFILE / AWS_REGION) to create the users and
read the webhook secret or Stripe key, so run it against dev, not prod. Each run adds three
users and two workspaces. Their emails and passwords go to
.aws-sam/smoke-users.json (gitignored) so you can sign in with them; they are
never printed.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote

import boto3
import requests
from botocore.exceptions import ClientError
import stripe
from pycognito import Cognito

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--stack", default="saas-dev")
STACK = parser.parse_args().stack
session = boto3.session.Session()
region = session.region_name
stack = session.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
out = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
API, POOL, CLIENT = out["ApiUrl"], out["UserPoolId"], out["UserPoolClientId"]
idp = session.client("cognito-idp")
RUN = int(time.time())

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""))
    if not ok:
        failures.append(name)
    return ok


def show(r: requests.Response) -> str:
    return f"{r.status_code} {r.text[:200]}"


def call(method: str, path: str, token: str | None = None, **kw) -> requests.Response:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, API + path, headers=headers, timeout=30, **kw)


def new_key() -> dict:
    """An Idempotency-Key header: a create that makes something needs a new one each time."""
    return {"Idempotency-Key": secrets.token_hex(16)}


def wait_for(fn, timeout: int = 90, every: float = 3):
    """Polls fn() until it returns something truthy (events are asynchronous)."""
    deadline = time.time() + timeout
    while True:
        result = fn()
        if result or time.time() > deadline:
            return result
        time.sleep(every)


class Person:
    """A test user in the user pool, signed in with SRP (the only flow the app client allows)."""

    def __init__(self, name: str):
        self.name = name
        self.email = f"smoke-{name}+{RUN}@example.com"
        self.password = "Sm0ke!" + secrets.token_urlsafe(18)
        idp.admin_create_user(UserPoolId=POOL, Username=self.email, MessageAction="SUPPRESS",
                              UserAttributes=[{"Name": "email", "Value": self.email},
                                              {"Name": "email_verified", "Value": "true"}])
        idp.admin_set_user_password(UserPoolId=POOL, Username=self.email, Password=self.password, Permanent=True)
        self.workspace: str | None = None   # the one they work in, sent as X-Tenant-Id (as a browser tab does)
        self.sign_in()
        self.id = self.claims["sub"]

    def sign_in(self) -> "Person":
        """New tokens; the pre-token trigger lists every workspace in them, with the role and plan."""
        self.cognito = Cognito(POOL, CLIENT, user_pool_region=region, username=self.email)
        self.cognito.authenticate(password=self.password)
        if self.workspace not in self.workspaces:   # left or removed: work in another, as the app does
            self.workspace = next(iter(self.workspaces), None)
        return self

    @property
    def token(self) -> str:
        return self.cognito.id_token

    @property
    def claims(self) -> dict:
        payload = self.token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    @property
    def workspaces(self) -> dict:
        """What the token lists: {tenant_id: {"role": ..., "plan": ...}}."""
        return json.loads(self.claims.get("workspaces") or "{}")

    def refresh_works(self) -> bool:
        try:
            self.cognito.renew_access_token()
            return True
        except ClientError:
            return False

    def __call__(self, method: str, path: str, tenant=..., **kw) -> requests.Response:
        """As this person, in `tenant` (X-Tenant-Id): by default the workspace they work in; None sends none."""
        tenant = self.workspace if tenant is ... else tenant
        headers = {**kw.pop("headers", {}), **({"X-Tenant-Id": tenant} if tenant else {})}
        return call(method, path, self.token, headers=headers, **kw)


def ssm_value(name: str) -> str:
    return session.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


STRIPE = params.get("PaymentProvider") == "stripe"
if STRIPE:
    stripe_key = ssm_value(params["StripeSecretKeyParam"])
    if stripe_key.startswith(("sk_live_", "rk_live_")):
        sys.exit("The stack's Stripe key is a live key: run this only against test mode.")
    stripe_api = stripe.StripeClient(stripe_key)
    PRICES = json.loads(ssm_value(params["StripeConfigParam"]))["prices"]
else:
    WEBHOOK_SECRET = ssm_value(params["PaymentWebhookSecretParam"])


def checkout_session(r: requests.Response):
    """The Stripe Checkout session behind a checkout_url, with its line items."""
    match = r.ok and re.search(r"/(cs_(?:test|live)_[A-Za-z0-9]+)", r.json().get("checkout_url", ""))
    return stripe_api.v1.checkout.sessions.retrieve(match.group(1), {"expand": ["line_items"]}) if match else None


def webhook_body(event_type: str, tenant_id: str, plan: str | None = None, created: int | None = None) -> str:
    return json.dumps({"id": f"evt_smoke_{secrets.token_hex(6)}", "type": event_type, "tenant_id": tenant_id,
                       "plan": plan, "created": created or int(time.time())})


def send_webhook(body: str, signature: str | None = None) -> requests.Response:
    signature = signature or hmac.new(WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return call("POST", "/webhooks/payments", data=body,
                headers={"X-Signature": signature, "Content-Type": "application/json"})


def titles(person: Person) -> list[str]:
    r = person("GET", "/notifications", params={"limit": 50})
    return sorted(n["title"] for n in r.json()) if r.ok else []


# ---------- users ----------
owner, bob, carol = Person("owner"), Person("bob"), Person("carol")
creds = Path(__file__).resolve().parent.parent / ".aws-sam" / "smoke-users.json"
creds.parent.mkdir(exist_ok=True)
creds.write_text(json.dumps({"stack": STACK, "users": [{"email": p.email, "password": p.password}
                                                        for p in (owner, bob, carol)]}, indent=2))
os.chmod(creds, 0o600)
print(f"Stack {STACK} in {region}\n  API {API}\n  users {owner.email}, {bob.email}, {carol.email}\n")

print("Auth")
check("no token -> 401", call("GET", "/orders").status_code == 401)
check("access token -> 401", call("GET", "/orders", owner.cognito.access_token).status_code == 401)
check("ID token without a workspace -> 403", owner("GET", "/orders").status_code == 403)
check("GET /tenants/me without a workspace -> 403", owner("GET", "/tenants/me").status_code == 403)

print("Workspace")
key = new_key()
r = owner("POST", "/tenants", json={"name": "Smoke Co"}, headers=key)
check("POST /tenants", r.status_code == 201, show(r))
tenant_id = r.json().get("tenant_id")
r = owner("POST", "/tenants", json={"name": "Smoke Co"}, headers=key)
check("a retry with the same Idempotency-Key returns the same workspace",
      r.status_code == 201 and r.json()["tenant_id"] == tenant_id, show(r))
check("a workspace without an Idempotency-Key -> 422",
      owner("POST", "/tenants", json={"name": "Smoke Co"}).status_code == 422)
owner.sign_in()
check("new token lists the workspace", owner.workspaces == {tenant_id: {"role": "owner", "plan": "free"}},
      owner.workspaces)
r = owner("GET", "/tenants/me")
check("GET /tenants/me", r.ok and r.json()["plan"] == "free" and r.json()["seats_used"] == 1, show(r))
r = owner("GET", "/tenants", tenant=None)
check("GET /tenants (no X-Tenant-Id needed)", r.ok and [w["tenant_id"] for w in r.json()] == [tenant_id], show(r))
check("invalid name -> 422", carol("POST", "/tenants", json={"name": ""}, headers=new_key()).status_code == 422)

print("Orders")
widget, key = {"item": "Widget", "quantity": 2, "price_cents": 1999}, new_key()
r = owner("POST", "/orders", json=widget, headers=key)
check("POST /orders", r.status_code == 201 and r.json()["status"] == "pending", show(r))
order1 = r.json()["order_id"]
r = owner("POST", "/orders", json=widget, headers=key)
check("a retry with the same Idempotency-Key returns the same order", r.status_code == 201
      and r.json()["order_id"] == order1, show(r))
check("an order without an Idempotency-Key -> 422", owner("POST", "/orders", json=widget).status_code == 422)
order2 = owner("POST", "/orders", json={"item": "Gadget", "quantity": 1, "price_cents": 500},
               headers=new_key()).json()["order_id"]
r = owner("GET", f"/orders/{order1}")
check("GET /orders/{id}", r.ok and r.json()["item"] == "Widget", show(r))
check("unknown order -> 404", owner("GET", "/orders/does-not-exist").status_code == 404)
check("invalid order -> 422",
      owner("POST", "/orders", json={"item": "", "quantity": 0, "price_cents": 1},
            headers=new_key()).status_code == 422)
r = owner("PATCH", f"/orders/{order1}/status", json={"status": "paid"})
check("PATCH status pending -> paid", r.ok and r.json()["status"] == "paid", show(r))
r = owner("PATCH", f"/orders/{order1}/status", json={"status": "pending"})
check("PATCH status paid -> pending refused (400)", r.status_code == 400, show(r))
r = owner("PATCH", f"/orders/{order1}/status", json={"status": "shipped"})
check("PATCH status paid -> shipped", r.ok and r.json()["status"] == "shipped", show(r))
r = owner("GET", "/orders", params={"limit": 1})
page1 = r.json() if r.ok else {}
check("GET /orders?limit=1 (newest first, has a cursor)",
      [o["order_id"] for o in page1.get("items", [])] == [order2] and bool(page1.get("next_cursor")), show(r))
r = owner("GET", "/orders", params={"limit": 1, "cursor": page1.get("next_cursor")})
check("GET /orders?cursor= (next page)", r.ok and [o["order_id"] for o in r.json()["items"]] == [order1], show(r))

print("Billing")
r = owner("GET", "/billing")
check("GET /billing (free)", r.ok and r.json()["plan"] == "free" and r.json()["limits"]["max_users"] == 3, show(r))
r = owner("POST", "/billing/checkout", json={"plan": "pro"})
check("POST /billing/checkout", r.ok and "checkout_url" in r.json(), show(r))
check("checkout with an unknown plan -> 422",
      owner("POST", "/billing/checkout", json={"plan": "gold"}).status_code == 422)
if STRIPE:
    check("it is Stripe Checkout",
          r.ok and r.json()["checkout_url"].startswith("https://checkout.stripe.com/"), show(r))
    session = checkout_session(r)
    check("the session subscribes this workspace to pro and returns to /billing", bool(session)
          and session.mode == "subscription" and session.client_reference_id == tenant_id
          and session.customer_email == owner.email and session.success_url.endswith("/billing")
          and [i.price.id for i in session.line_items.data] == [PRICES["pro"]],
          session and {"mode": session.mode, "ref": session.client_reference_id, "success": session.success_url})
    check("the checkout page loads", requests.get(r.json()["checkout_url"], timeout=30).ok)
    r = call("POST", "/webhooks/payments", data='{"id": "evt_fake"}',
             headers={"Stripe-Signature": "t=1,v1=bad", "Content-Type": "application/json"})
    check("webhook with a bad signature -> 400", r.status_code == 400, show(r))
    # What Checkout does once the card is accepted, through Stripe's API with its test card.
    customer = stripe_api.v1.customers.create({
        "email": owner.email, "payment_method": "pm_card_visa", "metadata": {"smoke_test": STACK},
        "invoice_settings": {"default_payment_method": "pm_card_visa"}})
    good_card = customer.invoice_settings.default_payment_method
    subscription = stripe_api.v1.subscriptions.create({
        "customer": customer.id, "items": [{"price": PRICES["pro"]}], "metadata": {"tenant_id": tenant_id}})
    check("Stripe: test subscription to pro is active", subscription.status == "active", subscription.status)
    summary = wait_for(lambda: (b := owner("GET", "/billing").json()).get("plan") == "pro" and b)
    check("Stripe's webhook made GET /billing pro", bool(summary), summary or owner("GET", "/billing").text)
    check("billing is manageable", bool(summary) and summary["manageable"] is True)
    r = owner("POST", "/billing/portal")
    check("POST /billing/portal -> Stripe's portal",
          r.ok and r.json()["portal_url"].startswith("https://billing.stripe.com/"), show(r))
    r = owner("POST", "/billing/checkout", json={"plan": "business"})
    check("upgrading a subscriber opens the portal's confirm page",
          r.ok and r.json()["checkout_url"].startswith("https://billing.stripe.com/"), show(r))
    check("buying the current plan again -> 409",
          owner("POST", "/billing/checkout", json={"plan": "pro"}).status_code == 409)
else:
    activated_at = int(time.time())
    body = webhook_body("subscription.activated", tenant_id, "pro", activated_at)
    check("webhook with a bad signature -> 400", send_webhook(body, signature="bad").status_code == 400)
    r = send_webhook(body)
    check("webhook subscription.activated (pro)", r.ok, show(r))
    check("same webhook again is accepted (ignored)", send_webhook(body).ok)
    r = send_webhook(webhook_body("subscription.activated", tenant_id, "business", activated_at - 60))
    check("older webhook arriving late is accepted (ignored)", r.ok, show(r))
    check("webhook with an unknown plan -> 400",
          send_webhook(webhook_body("subscription.activated", tenant_id, "gold")).status_code == 400)
    r = owner("GET", "/billing")
    check("GET /billing is pro (late business event ignored)", r.ok and r.json()["plan"] == "pro", show(r))

print("Events (outbox -> EventBridge -> workers)")
started = time.time()
tenant = wait_for(lambda: (t := owner("GET", "/tenants/me").json()).get("plan") == "pro" and t)
check("accounts worker set the workspace plan to pro", bool(tenant),
      f"{time.time() - started:.0f}s" if tenant else "timed out")
check("new token says plan=pro", owner.sign_in().workspaces[tenant_id]["plan"] == "pro", owner.workspaces)
expected = ["Order created", "Order created", "Plan changed to pro"]
got = wait_for(lambda: (t := titles(owner)) == expected and t)
check("notifications worker: 2 orders + plan change", got == expected, titles(owner))
note = owner("GET", "/notifications").json()[0]
r = owner("POST", f"/notifications/{note['notification_id']}/read")
check("POST /notifications/{id}/read", r.status_code == 204, show(r))
r = owner("GET", "/notifications")
check("notification is marked read",
      r.ok and any(n["notification_id"] == note["notification_id"] and n["read"] for n in r.json()), show(r))
check("unknown notification -> 404", owner("POST", "/notifications/nope/read").status_code == 404)

print("Invitations sent")
r = owner("POST", "/users/invite", json={"email": bob.email, "role": "member"})
check("POST /users/invite (bob)", r.status_code == 201, show(r))
check("inviting bob again -> 409", owner("POST", "/users/invite", json={"email": bob.email}).status_code == 409)
check("inviting yourself -> 409", owner("POST", "/users/invite", json={"email": owner.email}).status_code == 409)
check("invalid email -> 422", owner("POST", "/users/invite", json={"email": "not-an-email"}).status_code == 422)
check("POST /users/invite (carol)", owner("POST", "/users/invite", json={"email": carol.email}).status_code == 201)
ghost = f"smoke-ghost+{RUN}@example.com"
check("POST /users/invite (ghost)", owner("POST", "/users/invite", json={"email": ghost}).status_code == 201)
r = owner("DELETE", f"/users/invitations/{quote(ghost, safe='')}")
check("DELETE /users/invitations/{email}", r.status_code == 204, show(r))
check("cancelling it again -> 404",
      owner("DELETE", f"/users/invitations/{quote(ghost, safe='')}").status_code == 404)
r = owner("GET", "/users/invitations")
check("GET /users/invitations (bob, carol)",
      r.ok and sorted(i["email"] for i in r.json()) == sorted([bob.email, carol.email]), show(r))

print("Another workspace: isolation, several workspaces, decline")
r = carol("POST", "/tenants", json={"name": "Other Co"}, headers=new_key())
check("carol creates her own workspace", r.status_code == 201, show(r))
other_id = r.json().get("tenant_id")
carol.sign_in()
check("naming a workspace her token doesn't list -> 401",
      carol("GET", "/orders", tenant=tenant_id).status_code == 401)
check("carol can't read Smoke Co's order -> 404", carol("GET", f"/orders/{order1}").status_code == 404)
check("carol can't change Smoke Co's order -> 404",
      carol("PATCH", f"/orders/{order2}/status", json={"status": "cancelled"}).status_code == 404)
r = carol("GET", "/orders")
check("carol's order list is empty", r.ok and r.json()["items"] == [], show(r))
r = carol("GET", "/users")
check("carol's member list is only carol", r.ok and [m["user_id"] for m in r.json()] == [carol.id], show(r))
r = carol("GET", "/invitations")
check("GET /invitations (carol sees Smoke Co's invitation)",
      r.ok and [i["tenant_id"] for i in r.json()] == [tenant_id], show(r))
r = carol("POST", f"/invitations/{tenant_id}/accept")
check("carol joins Smoke Co too, keeping Other Co", r.ok, show(r))
carol.sign_in()
check("carol's new token lists both workspaces", set(carol.workspaces) == {tenant_id, other_id}, carol.workspaces)
r = carol("GET", "/orders", tenant=tenant_id)
check("in Smoke Co (X-Tenant-Id) carol sees its orders", r.ok and len(r.json()["items"]) == 2, show(r))
r = carol("GET", "/orders", tenant=other_id)
check("in Other Co she still sees none", r.ok and r.json()["items"] == [], show(r))
check("two workspaces, none named -> 403", carol("GET", "/orders", tenant=None).status_code == 403)
r = carol("DELETE", f"/users/{carol.id}", tenant=tenant_id)
check("carol leaves Smoke Co", r.status_code == 204, show(r))
check("leaving kept carol signed in", carol.refresh_works())
check("carol's new token lists only Other Co", list(carol.sign_in().workspaces) == [other_id], carol.workspaces)
check("re-invite carol", owner("POST", "/users/invite", json={"email": carol.email}).status_code == 201)
r = carol("POST", f"/invitations/{tenant_id}/decline")
check("POST /invitations/{tenant_id}/decline", r.status_code == 204, show(r))
check("declining again -> 404", carol("POST", f"/invitations/{tenant_id}/decline").status_code == 404)
r = owner("GET", "/users/invitations")
check("owner sees only bob's invitation now", r.ok and [i["email"] for i in r.json()] == [bob.email], show(r))

print("Joining and roles")
r = bob("GET", "/invitations")
check("bob sees his invitation", r.ok and [i["tenant_name"] for i in r.json()] == ["Smoke Co"], show(r))
r = bob("POST", f"/invitations/{tenant_id}/accept")
check("POST /invitations/{tenant_id}/accept", r.ok and r.json()["tenant_id"] == tenant_id, show(r))
check("bob's new token: member of Smoke Co on pro",
      bob.sign_in().workspaces == {tenant_id: {"role": "member", "plan": "pro"}}, bob.workspaces)
r = bob("GET", "/orders", params={"limit": 50})
check("bob sees the workspace's orders", r.ok and len(r.json()["items"]) == 2, show(r))
check("bob (member) can create an order",
      bob("POST", "/orders", json={"item": "Bolt", "quantity": 5, "price_cents": 10},
          headers=new_key()).status_code == 201)
check("bob (member) can't invite -> 403", bob("POST", "/users/invite", json={"email": ghost}).status_code == 403)
check("bob (member) can't change roles -> 403",
      bob("PATCH", f"/users/{owner.id}/role", json={"role": "member"}).status_code == 403)
check("bob (member) can't remove others -> 403", bob("DELETE", f"/users/{owner.id}").status_code == 403)
check("bob (member) can't start checkout -> 403",
      bob("POST", "/billing/checkout", json={"plan": "business"}).status_code == 403)
r = owner("GET", "/users")
check("GET /users (owner + bob)",
      r.ok and sorted(m["user_id"] for m in r.json()) == sorted([owner.id, bob.id]), show(r))
r = owner("GET", "/tenants/me")
check("seats_used is 2", r.ok and r.json()["seats_used"] == 2, show(r))
r = owner("PATCH", f"/users/{bob.id}/role", json={"role": "admin"})
check("PATCH /users/{id}/role (bob -> admin)", r.ok and r.json()["role"] == "admin", show(r))
check("bob was signed out (refresh token revoked)", not bob.refresh_works())
check("owner's role can't be changed -> 403",
      owner("PATCH", f"/users/{owner.id}/role", json={"role": "admin"}).status_code == 403)
check("role 'owner' can't be given -> 422",
      owner("PATCH", f"/users/{bob.id}/role", json={"role": "owner"}).status_code == 422)
check("bob's new token says admin", bob.sign_in().workspaces[tenant_id]["role"] == "admin")
r = bob("POST", "/users/invite", json={"email": ghost})
check("bob (admin) can invite", r.status_code == 201, show(r))
r = bob("DELETE", f"/users/invitations/{quote(ghost, safe='')}")
check("bob (admin) can cancel an invitation", r.status_code == 204, show(r))
check("bob (admin) can't start checkout -> 403",
      bob("POST", "/billing/checkout", json={"plan": "business"}).status_code == 403)
r = owner("PATCH", f"/users/{bob.id}/role", json={"role": "member"})
check("PATCH /users/{id}/role (bob -> member)", r.ok and r.json()["role"] == "member", show(r))
old_token = bob.token  # issued while bob was admin
check("bob's older admin token can't invite (role is checked in the DB) -> 403",
      call("POST", "/users/invite", old_token, headers={"X-Tenant-Id": tenant_id},
           json={"email": ghost}).status_code == 403)

print("Leaving and removing")
check("owner can't leave -> 403", owner("DELETE", f"/users/{owner.id}").status_code == 403)
bob.sign_in()
r = bob("DELETE", f"/users/{bob.id}")
check("DELETE /users/{own id} (bob leaves)", r.status_code == 204, show(r))
check("bob's old token is refused by accounts -> 403", bob("GET", "/users").status_code == 403)
check("leaving kept bob signed in", bob.refresh_works())
check("bob's new token has no workspace", bob.sign_in().workspaces == {}, bob.workspaces)
r = owner("GET", "/users")
check("owner sees only themself", r.ok and [m["user_id"] for m in r.json()] == [owner.id], show(r))
check("re-invite bob", owner("POST", "/users/invite", json={"email": bob.email}).status_code == 201)
check("bob accepts again", bob("POST", f"/invitations/{tenant_id}/accept").ok)
r = owner("DELETE", f"/users/{bob.id}")
check("DELETE /users/{id} (owner removes bob)", r.status_code == 204, show(r))
check("removing bob again -> 404", owner("DELETE", f"/users/{bob.id}").status_code == 404)
check("bob was signed out", not bob.refresh_works())
r = owner("GET", "/tenants/me")
check("seats_used is back to 1", r.ok and r.json()["seats_used"] == 1, show(r))

print("Billing lifecycle")
if STRIPE:
    # A card that is refused when charged, then an upgrade charged at once: the payment fails.
    card = stripe_api.v1.payment_methods.attach("pm_card_chargeCustomerFail", {"customer": customer.id})
    stripe_api.v1.customers.update(customer.id, {"invoice_settings": {"default_payment_method": card.id}})
    item = subscription["items"]["data"][0]
    updated = stripe_api.v1.subscriptions.update(subscription.id, {
        "items": [{"id": item["id"], "price": PRICES["business"]}],
        "proration_behavior": "always_invoice", "payment_behavior": "allow_incomplete"})
    check("Stripe: the upgrade's payment fails", updated.status == "past_due", updated.status)
    summary = wait_for(lambda: (b := owner("GET", "/billing").json()).get("status") == "past_due" and b)
    check("GET /billing: past_due, still pro", bool(summary) and summary["plan"] == "pro",
          summary or owner("GET", "/billing").text)
    # The owner fixes the card: the open invoice is paid and the upgrade takes effect.
    stripe_api.v1.customers.update(customer.id, {"invoice_settings": {"default_payment_method": good_card}})
    invoice = stripe_api.v1.invoices.pay(updated.latest_invoice, {"payment_method": good_card})
    check("Stripe: the open invoice is paid with a good card", invoice.status == "paid", invoice.status)
    summary = wait_for(lambda: (b := owner("GET", "/billing").json()).get("status") == "active" and b)
    check("GET /billing: business, active (the plan change took effect)", bool(summary) and
          summary["plan"] == "business", summary or owner("GET", "/billing").text)
    stripe_api.v1.subscriptions.cancel(subscription.id)
    summary = wait_for(lambda: (b := owner("GET", "/billing").json()).get("status") == "cancelled" and b)
    check("GET /billing: free, cancelled", bool(summary) and summary["plan"] == "free",
          summary or owner("GET", "/billing").text)
    session = checkout_session(owner("POST", "/billing/checkout", json={"plan": "pro"}))
    check("subscribing again uses the same Stripe customer", bool(session) and session.customer == customer.id,
          session and session.customer)
else:
    r = send_webhook(webhook_body("payment.failed", tenant_id))
    check("webhook payment.failed", r.ok, show(r))
    r = owner("GET", "/billing")
    check("GET /billing: past_due, still pro", r.ok and (r.json()["plan"], r.json()["status"]) == ("pro", "past_due"),
          show(r))
    time.sleep(1)  # the cancel must be newer than the payment failure
    r = send_webhook(webhook_body("subscription.cancelled", tenant_id))
    check("webhook subscription.cancelled", r.ok, show(r))
    r = owner("GET", "/billing")
    check("GET /billing: free, cancelled", r.ok and (r.json()["plan"], r.json()["status"]) == ("free", "cancelled"),
          show(r))
started = time.time()
tenant = wait_for(lambda: (t := owner("GET", "/tenants/me").json()).get("subscription_status") == "cancelled" and t)
check("accounts worker: workspace back to free, cancelled",
      bool(tenant) and tenant["plan"] == "free", f"{time.time() - started:.0f}s" if tenant else "timed out")
got = wait_for(lambda: {"Payment failed", "Plan changed to free"} <= set(t := titles(owner)) and t)
check("notifications: payment failed + plan changed to free", bool(got), titles(owner))

print("Plan limits (free: 3 users)")
for name in ("seat1", "seat2"):
    email = f"smoke-{name}+{RUN}@example.com"
    check(f"invite {name}", owner("POST", "/users/invite", json={"email": email}).status_code == 201)
r = owner("POST", "/users/invite", json={"email": f"smoke-seat3+{RUN}@example.com"})
check("a 4th seat on free -> 402", r.status_code == 402, show(r))

if STRIPE:
    plan_changes = [t for t in titles(owner) if t.startswith("Plan changed")]
    check("each plan change notified once, however many updates Stripe sent",
          sorted(plan_changes) == ["Plan changed to business", "Plan changed to free", "Plan changed to pro"],
          plan_changes)
    stripe_api.v1.customers.delete(customer.id)   # test mode: leave nothing behind in Stripe

print("Deleting a workspace")
r = carol("POST", "/orders", tenant=other_id, json={"item": "Doomed", "quantity": 1, "price_cents": 100},
          headers=new_key())
check("an order in Other Co", r.status_code == 201, show(r))
r = carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "other co"})
check("deleting needs the exact name -> 400", r.status_code == 400, show(r))
check("only its owner deletes it -> 403",
      owner("DELETE", "/tenants/me", json={"name": "Smoke Co"}).status_code == 403)
r = carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "Other Co"})
check("DELETE /tenants/me", r.ok and r.json()["status"] == "deleting", show(r))
check("her new token no longer lists it", other_id not in carol.sign_in().workspaces, carol.workspaces)
r = carol("GET", "/tenants", tenant=None)
check("GET /tenants lists it as deleting",
      r.ok and {w["tenant_id"]: w["status"] for w in r.json()}.get(other_id) == "deleting", show(r))
r = carol("POST", f"/tenants/{other_id}/restore", tenant=None)
check("POST /tenants/{id}/restore", r.ok and r.json()["status"] == "active", show(r))
check("restored: her token lists it again", other_id in carol.sign_in().workspaces, carol.workspaces)
if params.get("TenantDeletionDays") == "0":
    carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "Other Co"})
    job = session.client("cloudformation").describe_stack_resource(
        StackName=STACK, LogicalResourceId="AccountsPurgeFunction")["StackResourceDetail"]["PhysicalResourceId"]
    result = session.client("lambda").invoke(FunctionName=job)   # as the hourly schedule does, but now
    ran = json.loads(result["Payload"].read())
    check("the purge job removes it", "FunctionError" not in result and ran.get("purged", 0) >= 1, ran)
    r = carol("GET", "/tenants", tenant=None)
    check("GET /tenants no longer lists it", r.ok and other_id not in {w["tenant_id"] for w in r.json()}, show(r))
    r = carol("POST", "/tenants", json={"name": "After Co"}, headers=new_key())
    check("her workspace count went down (she can create another)", r.status_code == 201, show(r))
else:
    print("  (purge not checked: TenantDeletionDays isn't 0 on this stack)")

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
print(f"Test users saved to {creds}")
sys.exit(1 if failures else 0)
