# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3", "pycognito", "requests"]
# ///
"""
Smoke test for a deployed stack, run after sam deploy. Creates a test user,
then goes through the main flow end to end and prints PASS/FAIL per check:

  auth -> workspace -> orders -> checkout + signed webhook (upgrade to pro)
  -> outbox -> EventBridge -> accounts worker (plan in the tenant + token)
  -> notifications worker (in-app notifications) -> invitations

    uv run scripts/smoke-test.py [--stack saas-dev]

Uses your AWS credentials (AWS_PROFILE / AWS_REGION) to create the user and
read the webhook secret, so run it against dev, not prod. Each run adds one
user and workspace. Their email and password go to .aws-sam/smoke-user.json
(gitignored) so you can sign in with them; they are never printed.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path

import boto3
import requests
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

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""))
    if not ok:
        failures.append(name)
    return ok


def call(method: str, path: str, token: str | None = None, **kw) -> requests.Response:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, API + path, headers=headers, timeout=30, **kw)


def claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def wait_for(fn, timeout: int = 90, every: float = 3):
    """Polls fn() until it returns something truthy (events are asynchronous)."""
    deadline = time.time() + timeout
    while True:
        result = fn()
        if result or time.time() > deadline:
            return result
        time.sleep(every)


# ---------- test user ----------
ts = int(time.time())
email = f"smoke+{ts}@example.com"
password = "Sm0ke!" + secrets.token_urlsafe(18)
idp.admin_create_user(UserPoolId=POOL, Username=email, MessageAction="SUPPRESS",
                      UserAttributes=[{"Name": "email", "Value": email},
                                      {"Name": "email_verified", "Value": "true"}])
idp.admin_set_user_password(UserPoolId=POOL, Username=email, Password=password, Permanent=True)
creds = Path(__file__).resolve().parent.parent / ".aws-sam" / "smoke-user.json"
creds.parent.mkdir(exist_ok=True)
creds.write_text(json.dumps({"stack": STACK, "email": email, "password": password}, indent=2))
os.chmod(creds, 0o600)


def sign_in() -> Cognito:
    user = Cognito(POOL, CLIENT, user_pool_region=region, username=email)
    user.authenticate(password=password)  # SRP, the only flow the app client allows
    return user


print(f"Stack {STACK} in {region}\n  API {API}\n  test user {email}\n")

print("Auth")
u = sign_in()
check("no token -> 401", call("GET", "/orders").status_code == 401)
check("access token rejected -> 401", call("GET", "/orders", u.access_token).status_code == 401)
r = call("GET", "/orders", u.id_token)
check("ID token without a workspace -> 403", r.status_code == 403, r.status_code)

print("Workspace")
r = call("POST", "/tenants", u.id_token, json={"name": "Smoke Co"})
check("create workspace", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")
tenant_id = r.json().get("tenant_id") if r.ok else None
u = sign_in()  # the pre-token trigger adds the workspace claims to new tokens
c = claims(u.id_token)
check("token has workspace claims",
      c.get("tenant_id") == tenant_id and c.get("role") == "owner" and c.get("plan") == "free",
      {k: c.get(k) for k in ("tenant_id", "role", "plan")})
r = call("GET", "/tenants/me", u.id_token)
check("GET /tenants/me", r.ok and r.json().get("plan") == "free", f"{r.status_code} {r.text[:200]}")
check("second workspace refused", call("POST", "/tenants", u.id_token, json={"name": "Again"}).status_code >= 400)

print("Orders")
r = call("POST", "/orders", u.id_token, json={"item": "Widget", "quantity": 2, "price_cents": 1999})
check("create order", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")
order_id = r.json().get("order_id") if r.ok else None
r = call("GET", f"/orders/{order_id}", u.id_token)
check("get order", r.ok and r.json().get("status") == "pending", r.status_code)
r = call("PATCH", f"/orders/{order_id}/status", u.id_token, json={"status": "paid"})
check("update status to paid", r.ok and r.json().get("status") == "paid", f"{r.status_code} {r.text[:200]}")
r = call("GET", "/orders", u.id_token)
check("list orders", r.ok and [o["order_id"] for o in r.json()["items"]] == [order_id], r.status_code)
check("invalid order -> 422", call("POST", "/orders", u.id_token, json={"item": "", "quantity": 0}).status_code == 422)

print("Billing")
r = call("GET", "/billing", u.id_token)
check("GET /billing is free", r.ok and r.json().get("plan") == "free", f"{r.status_code} {r.text[:200]}")
r = call("POST", "/billing/checkout", u.id_token, json={"plan": "pro"})
check("checkout url", r.ok and "checkout_url" in r.json(), f"{r.status_code} {r.text[:200]}")

secret = session.client("ssm").get_parameter(Name=params["PaymentWebhookSecretParam"],
                                             WithDecryption=True)["Parameter"]["Value"]
body = json.dumps({"id": f"evt_smoke_{ts}", "type": "subscription.activated", "tenant_id": tenant_id,
                   "plan": "pro", "created": int(time.time())})
signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
r = call("POST", "/webhooks/payments", data=body, headers={"X-Signature": "bad", "Content-Type": "application/json"})
check("webhook with a bad signature -> 400", r.status_code == 400, r.status_code)
r = call("POST", "/webhooks/payments", data=body,
         headers={"X-Signature": signature, "Content-Type": "application/json"})
check("signed webhook accepted", r.ok, f"{r.status_code} {r.text[:200]}")
r = call("POST", "/webhooks/payments", data=body,
         headers={"X-Signature": signature, "Content-Type": "application/json"})
check("same webhook again is accepted (ignored)", r.ok, r.status_code)
r = call("GET", "/billing", u.id_token)
check("billing is pro", r.ok and r.json().get("plan") == "pro", f"{r.status_code} {r.text[:200]}")

print("Events (outbox -> EventBridge -> workers)")
started = time.time()
tenant = wait_for(lambda: (t := call("GET", "/tenants/me", u.id_token).json()).get("plan") == "pro" and t)
check("accounts worker set the workspace plan to pro", bool(tenant),
      f"{time.time() - started:.0f}s" if tenant else "timed out")
u = sign_in()
check("new token says plan=pro", claims(u.id_token).get("plan") == "pro", claims(u.id_token).get("plan"))
started = time.time()
notes = wait_for(lambda: len(n := call("GET", "/notifications", u.id_token).json()) >= 2 and n)
titles = sorted(n["title"] for n in notes) if notes else []
check("notifications worker created 2 notifications", titles == ["Order created", "Plan changed to pro"],
      f"{titles} after {time.time() - started:.0f}s")
if notes:
    r = call("POST", f"/notifications/{notes[0]['notification_id']}/read", u.id_token)
    check("mark notification read", r.status_code == 204, r.status_code)

print("Invitations")
invitee = f"invitee+{ts}@example.com"
r = call("POST", "/users/invite", u.id_token, json={"email": invitee, "role": "member"})
check("invite a member", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")
r = call("GET", "/users/invitations", u.id_token)
check("pending invitation listed", r.ok and any(i.get("email") == invitee for i in r.json()), r.status_code)
r = call("GET", "/users", u.id_token)
check("members list has the owner only", r.ok and len(r.json()) == 1, f"{r.status_code} {r.text[:200]}")

print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
print(f"Test user saved to {creds}")
sys.exit(1 if failures else 0)
