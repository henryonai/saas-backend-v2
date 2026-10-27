"""Builds API Gateway events and calls a module's Lambda handler as a given user."""
import json

# Default for `tenant_id` below: act in the token's workspace when it lists exactly one.
ONLY_WORKSPACE = object()


class FakeContext:
    function_name = "test"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:ap-southeast-1:123456789012:function:test"
    aws_request_id = "test-request"

    def get_remaining_time_in_millis(self) -> int:
        return 30_000


def claims(user_id: str, tenant_id: str | None = None, role: str | None = None,
           plan: str = "free", email_verified: bool = True,
           workspaces: dict[str, tuple[str, str]] | None = None) -> dict:
    """
    ID token claims as the accounts login trigger would produce them: the
    user's workspace `tenant_id` (their `role` there, its `plan`), and any
    others as {tenant_id: (role, plan)}.

    The result also keeps tenant_id, role and plan as plain keys, for tests to
    read (owner["tenant_id"]). Real tokens don't have them, and the backend
    reads only the "workspaces" claim.
    """
    c = {"sub": user_id, "email": f"{user_id}@example.com", "token_use": "id",
         "email_verified": "true" if email_verified else "false"}
    listed = {**(workspaces or {}), **({tenant_id: (role, plan)} if tenant_id else {})}
    if listed:
        c["workspaces"] = json.dumps({t: {"role": r, "plan": p} for t, (r, p) in listed.items()})
    if tenant_id:
        c.update(tenant_id=tenant_id, role=role, plan=plan)
    return c


def only_workspace(user: dict | None) -> str | None:
    """The tenant id of the token's workspace, if it lists exactly one (a broken claim: none)."""
    try:
        listed = json.loads((user or {}).get("workspaces") or "{}")
    except ValueError:
        return None   # a test of a malformed claim: the backend refuses it
    return next(iter(listed)) if isinstance(listed, dict) and len(listed) == 1 else None


def http_event(method: str, path: str, user: dict | None = None,
               body: dict | None = None, query: dict | None = None,
               tenant_id: str | None | object = ONLY_WORKSPACE) -> dict:
    """`tenant_id` is the X-Tenant-Id header: by default the token's only workspace; None sends none."""
    request_context = {
        "http": {"method": method, "path": path, "sourceIp": "127.0.0.1"},
        "requestId": "test",
        "stage": "$default",
    }
    if user is not None:
        request_context["authorizer"] = {"jwt": {"claims": user, "scopes": []}}
    headers = {"content-type": "application/json"}
    tenant = only_workspace(user) if tenant_id is ONLY_WORKSPACE else tenant_id
    if tenant:
        headers["x-tenant-id"] = tenant
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "&".join(f"{k}={v}" for k, v in (query or {}).items()),
        "queryStringParameters": query,
        "headers": headers,
        "requestContext": request_context,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def call(handler, method: str, path: str, user: dict | None = None,
         body: dict | None = None, query: dict | None = None,
         tenant_id: str | None | object = ONLY_WORKSPACE):
    """Returns (status_code, parsed_json_or_None)."""
    response = handler(http_event(method, path, user, body, query, tenant_id), FakeContext())
    raw = response.get("body")
    return response["statusCode"], (json.loads(raw) if raw else None)
