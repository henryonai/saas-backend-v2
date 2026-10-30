# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3", "pycognito", "requests", "stripe>=16"]
# ///
"""
Smoke test for a deployed stack, run after sam deploy. Creates three test
users and calls every endpoint, printing PASS/FAIL per check:

  auth, workspaces, orders (status rules, paging), billing (Stripe Checkout,
  signed webhooks, the portal, an upgrade, payment failed, cancel),
  events between modules (outbox -> EventBridge -> accounts and notifications
  workers), invitations (send, cancel, accept, decline), roles and who may do
  what, leaving and removing members, tenant isolation, users in several
  workspaces (X-Tenant-Id), plan limits, and deleting, restoring and (with
  TenantDeletionDays of a few minutes at most, as on dev) purging a workspace
  (its Stripe subscription stops renewing, renews again, then is cancelled),
  deleting an account, exports (a workspace's and a user's), the audit log and
  the back-office.

    uv run scripts/smoke-test.py [--stack saas-dev]

Billing goes through Stripe's test mode: it subscribes with Stripe's test
cards through Stripe's API, and Stripe's own webhooks change the plan. It
needs scripts/stripe-setup.py to have run for the stack.

Uses your AWS credentials (AWS_PROFILE / AWS_REGION) to create the users and
read the Stripe key, so run it against dev, not prod. Each run adds three
users and two workspaces. Their emails and passwords go to
.aws-sam/smoke-users.json (gitignored) so you can sign in with them; they are
never printed.
"""
import argparse
import base64
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
WEB = out["WebUrl"]   # the app, and the API at WEB/api through CloudFront
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


stripe_key = ssm_value(params["StripeSecretKeyParam"])
if stripe_key.startswith(("sk_live_", "rk_live_")):
    sys.exit("The stack's Stripe key is a live key: run this only against test mode.")
stripe_api = stripe.StripeClient(stripe_key)
PRICES = json.loads(ssm_value(params["StripeConfigParam"]))["prices"]
def checkout_session(r: requests.Response):
    """The Stripe Checkout session behind a checkout_url, with its line items."""
    match = r.ok and re.search(r"/(cs_(?:test|live)_[A-Za-z0-9]+)", r.json().get("checkout_url", ""))
    return stripe_api.v1.checkout.sessions.retrieve(match.group(1), {"expand": ["line_items"]}) if match else None


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

print("The app's address: the API at /api through CloudFront, the app's routes")


def via_app(method: str, path: str, person: Person | None = None, tenant=..., **kw) -> requests.Response:
    """As the deployed app calls the API: WebUrl/api, with the person's token and workspace."""
    headers = kw.pop("headers", {})
    if person:
        headers["Authorization"] = f"Bearer {person.token}"
        workspace = person.workspace if tenant is ... else tenant
        if workspace:
            headers["X-Tenant-Id"] = workspace
    return requests.request(method, f"{WEB}/api{path}", headers=headers, timeout=30, **kw)


def is_json(r: requests.Response) -> bool:
    return r.headers.get("content-type", "").startswith("application/json")


r = via_app("GET", "/tenants/me", owner)
check("GET /api/tenants/me: the token and X-Tenant-Id go through",
      r.ok and r.json().get("tenant_id") == tenant_id, show(r))
r = via_app("POST", "/orders", owner, json={"item": ""}, headers=new_key())
check("POST /api/orders: the body goes through (an invalid one -> 422)", r.status_code == 422 and is_json(r), show(r))
r = via_app("GET", "/tenants/me")
check("no token -> 401 from the API, not the app's page", r.status_code == 401 and is_json(r), show(r))
r = via_app("GET", "/admin/tenants", owner)
check("not a platform admin -> 403 from the API, not the app's page", r.status_code == 403 and is_json(r), show(r))
r = via_app("GET", "/no-such-route", owner)
check("an unknown API route -> 404 from the API, not the app's page", r.status_code == 404 and is_json(r), show(r))
r = requests.get(f"{WEB}/orders/123", timeout=30)
check("an app route (/orders/123) -> the app's page",
      r.ok and r.headers.get("content-type", "").startswith("text/html"),
      f"the app isn't published: run web/scripts/deploy.sh {STACK}" if "AccessDenied" in r.text else show(r))

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
check("it is Stripe Checkout",
      r.ok and r.json()["checkout_url"].startswith("https://checkout.stripe.com/"), show(r))
checkout = checkout_session(r)
check("the session subscribes this workspace to pro and returns to /billing", bool(checkout)
      and checkout.mode == "subscription" and checkout.client_reference_id == tenant_id
      and checkout.customer_email == owner.email and checkout.success_url.endswith("/billing")
      and [i.price.id for i in checkout.line_items.data] == [PRICES["pro"]],
      checkout and {"mode": checkout.mode, "ref": checkout.client_reference_id, "success": checkout.success_url})
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
checkout = checkout_session(owner("POST", "/billing/checkout", json={"plan": "pro"}))
check("subscribing again uses the same Stripe customer", bool(checkout) and checkout.customer == customer.id,
      checkout and checkout.customer)

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

plan_changes = [t for t in titles(owner) if t.startswith("Plan changed")]
check("each plan change notified once, however many updates Stripe sent",
      sorted(plan_changes) == ["Plan changed to business", "Plan changed to free", "Plan changed to pro"],
      plan_changes)
stripe_api.v1.customers.delete(customer.id)   # test mode: leave nothing behind in Stripe

print("Audit log")
types = lambda: {e["type"] for e in owner("GET", "/audit").json().get("items", [])}   # noqa: E731
got = wait_for(lambda: {"TenantCreated", "UserInvited", "RoleChanged", "OrderCreated"} <= (t := types()) and t)
check("GET /audit: who created, invited, changed roles, ordered", bool(got), types())
r = owner("GET", "/audit")
promoted = next((e for e in r.json().get("items", []) if e["type"] == "RoleChanged"), None) if r.ok else None
check("an entry says who did it", bool(promoted) and promoted["actor_email"] == owner.email, promoted)
check("without a workspace named -> 403", owner("GET", "/audit", tenant=None).status_code == 403)

print("Exports")
r = owner("POST", "/exports/workspace")
check("POST /exports/workspace",
      r.status_code == 202 and r.json()["parts"] == ["accounts", "orders", "billing", "audit"], show(r))
started = time.time()
job = wait_for(lambda: (j := owner("GET", "/exports/workspace").json()) and j["status"] == "done" and j)
check("every module wrote its part, and the export is done", bool(job),
      f"{time.time() - started:.0f}s" if job else owner("GET", "/exports/workspace").text)
if job:
    orders = requests.get(job["files"]["orders"], timeout=30)
    check("the orders part downloads, with the workspace's orders",
          orders.ok and {o["order_id"] for o in orders.json()} >= {order1, order2}, orders.status_code)
    got = wait_for(lambda: "Export ready" in (t := titles(owner)) and t)
    check("notifications: export ready", bool(got), titles(owner))
check("only the owner exports a workspace -> 403",
      bob("POST", "/exports/workspace", tenant=tenant_id).status_code in (401, 403))
r = carol("POST", "/exports/me", tenant=None)
check("POST /exports/me", r.status_code == 202, show(r))
job = wait_for(lambda: (j := carol("GET", "/exports/me", tenant=None).json()) and j["status"] == "done" and j)
check("carol's own export is done", bool(job), carol("GET", "/exports/me", tenant=None).text)
if job:
    mine = requests.get(job["files"]["accounts"], timeout=30)
    check("it lists her workspaces", mine.ok and mine.json()["user"]["email"] == carol.email, mine.status_code)

print("Back-office (platform admins)")
ops = Person("ops")
idp.admin_add_user_to_group(UserPoolId=POOL, Username=ops.email, GroupName="platform-admins")
ops.sign_in()
check("a workspace owner can't open it -> 403", owner("GET", "/admin/tenants", tenant=None).status_code == 403)
summary = wait_for(lambda: (r := ops("GET", f"/admin/tenants/{other_id}", tenant=None)).ok and r.json())
check("GET /admin/tenants/{id}: Other Co's summary, from events",
      bool(summary) and summary["name"] == "Other Co" and summary["owner_id"] == carol.id, summary)
carol.sign_in()
before = carol.token   # issued while Other Co is active: it lists Other Co for up to 15 minutes more


def order_in_other_co(token: str) -> requests.Response:
    return call("POST", "/orders", token, json=widget,
                headers={"X-Tenant-Id": other_id, **new_key()})


r = ops("POST", f"/admin/tenants/{other_id}/suspend", tenant=None, json={"reason": "smoke test"})
check("POST .../suspend", r.status_code == 202, show(r))
gone = wait_for(lambda: other_id not in carol.sign_in().workspaces)
check("a suspended workspace leaves its owner's token", bool(gone), carol.workspaces)
# A requests.Response is falsy for a 4xx: poll the text, not the response.
refused = wait_for(lambda: (r := order_in_other_co(before)).status_code == 403 and r.text)
check("a token issued before the suspension can't add an order -> 403", bool(refused) and "suspended" in refused,
      refused or show(order_in_other_co(before)))
r = ops("POST", f"/admin/tenants/{other_id}/plan", tenant=None, json={"plan": "pro", "note": "smoke test"})
check("POST .../plan (grant pro)", r.status_code == 202, show(r))
back = wait_for(lambda: ops("GET", f"/admin/tenants/{other_id}", tenant=None).json().get("status") == "suspended"
                and ops("POST", f"/admin/tenants/{other_id}/unsuspend", tenant=None).status_code == 202)
check("POST .../unsuspend", bool(back))
back = wait_for(lambda: (w := carol.sign_in().workspaces.get(other_id)) and w["plan"] == "pro" and w)
check("unsuspended, it's back in her token, on the granted plan", bool(back), carol.workspaces)
added = wait_for(lambda: carol("POST", "/orders", tenant=other_id, json=widget, headers=new_key()).status_code == 201)
check("unsuspended, it takes orders again", bool(added))
r = ops("GET", "/admin/log", tenant=None)
check("GET /admin/log has the three actions", r.ok and [a["action"] for a in r.json()["items"]][:3] ==
      ["unsuspend", "grant_plan", "suspend"], show(r))

print("Deleting a workspace")
r = carol("POST", "/orders", tenant=other_id, json={"item": "Doomed", "quantity": 1, "price_cents": 100},
          headers=new_key())
check("an order in Other Co", r.status_code == 201, show(r))
# A paid subscription, so deleting it can be seen to stop the renewal.
other_customer = stripe_api.v1.customers.create({
    "email": carol.email, "payment_method": "pm_card_visa", "metadata": {"smoke_test": STACK},
    "invoice_settings": {"default_payment_method": "pm_card_visa"}})
other_sub = stripe_api.v1.subscriptions.create({
    "customer": other_customer.id, "items": [{"price": PRICES["pro"]}], "metadata": {"tenant_id": other_id}})
summary = wait_for(lambda: (b := carol("GET", "/billing", tenant=other_id).json()).get("manageable") and b)
check("Other Co subscribes to pro in Stripe", bool(summary), summary or carol("GET", "/billing", tenant=other_id).text)


def renews() -> bool:
    return not stripe_api.v1.subscriptions.retrieve(other_sub.id).cancel_at_period_end


r = carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "other co"})
check("deleting needs the exact name -> 400", r.status_code == 400, show(r))
check("someone not in it can't delete it -> 401",
      owner("DELETE", "/tenants/me", tenant=other_id, json={"name": "Other Co"}).status_code == 401)
r = carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "Other Co"})
check("DELETE /tenants/me", r.ok and r.json()["status"] == "deleting", show(r))
check("her new token no longer lists it", other_id not in carol.sign_in().workspaces, carol.workspaces)
r = carol("GET", "/tenants", tenant=None)
check("GET /tenants lists it as deleting",
      r.ok and {w["tenant_id"]: w["status"] for w in r.json()}.get(other_id) == "deleting", show(r))
check("Stripe: its subscription no longer renews", bool(wait_for(lambda: not renews())))
r = carol("POST", f"/tenants/{other_id}/restore", tenant=None)
check("POST /tenants/{id}/restore", r.ok and r.json()["status"] == "active", show(r))
check("restored: her token lists it again", other_id in carol.sign_in().workspaces, carol.workspaces)
check("Stripe: restored, it renews again", bool(wait_for(renews)))
grace = float(params.get("TenantDeletionDays", "7")) * 24 * 3600
if grace <= 300:   # a test stack's few seconds or minutes (dev: about 90 seconds)
    carol("DELETE", "/tenants/me", tenant=other_id, json={"name": "Other Co"})
    job = session.client("cloudformation").describe_stack_resource(
        StackName=STACK, LogicalResourceId="AccountsPurgeFunction")["StackResourceDetail"]["PhysicalResourceId"]

    def purge() -> dict:
        result = session.client("lambda").invoke(FunctionName=job)   # as the hourly schedule does, but now
        return {"error": True} if "FunctionError" in result else json.loads(result["Payload"].read())

    def gone() -> bool:
        listed = carol("GET", "/tenants", tenant=None)
        return listed.ok and other_id not in {w["tenant_id"] for w in listed.json()}

    # Until this workspace is gone: a run moments earlier leaves its own deleted ones for this purge too.
    ran = wait_for(lambda: "error" not in purge() and gone(), timeout=int(grace) + 60, every=10)
    check("once its time to restore has passed, the purge job removes it (GET /tenants no longer lists it)",
          bool(ran), carol("GET", "/tenants", tenant=None).text[:200])
    r = carol("POST", "/tenants", json={"name": "After Co"}, headers=new_key())
    check("her workspace count went down (she can create another)", r.status_code == 201, show(r))
    ended = wait_for(lambda: (st := stripe_api.v1.subscriptions.retrieve(other_sub.id).status) == "canceled" and st)
    check("Stripe: purged, its subscription is cancelled", bool(ended), ended)
else:
    print("  (purge not checked: TenantDeletionDays is more than a few minutes on this stack)")
stripe_api.v1.customers.delete(other_customer.id)   # test mode: leave nothing behind in Stripe

print("Deleting an account")
doomed = Person("doomed")
r = doomed("POST", "/tenants", tenant=None, json={"name": "Doomed Co"}, headers=new_key())
check("they create a workspace", r.status_code == 201, show(r))
doomed.sign_in()
r = doomed("DELETE", "/account", tenant=None, json={"email": "someone@example.com"})
check("deleting needs their exact email -> 400", r.status_code == 400, show(r))
r = doomed("DELETE", "/account", tenant=None, json={"email": doomed.email})
check("an owner of a workspace can't -> 409", r.status_code == 409 and "Doomed Co" in r.text, show(r))
r = doomed("DELETE", "/tenants/me", json={"name": "Doomed Co"})
check("they delete their workspace first", r.ok, show(r))
r = doomed("DELETE", "/account", tenant=None, json={"email": doomed.email})
check("DELETE /account", r.status_code == 204, show(r))
try:
    doomed.sign_in()
    check("their login is gone", False, "signed in")
except Exception as e:   # pycognito raises Cognito's UserNotFoundException / NotAuthorizedException
    check("their login is gone", "NotFound" in type(e).__name__ or "NotAuthorized" in type(e).__name__,
          type(e).__name__)

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
print(f"Test users saved to {creds}")
sys.exit(1 if failures else 0)
