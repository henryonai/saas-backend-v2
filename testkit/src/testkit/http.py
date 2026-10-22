"""Builds API Gateway events and calls a module's Lambda handler as a given user."""
import json


class FakeContext:
    function_name = "test"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:ap-southeast-1:123456789012:function:test"
    aws_request_id = "test-request"

    def get_remaining_time_in_millis(self) -> int:
        return 30_000


def claims(user_id: str, tenant_id: str | None = None, role: str | None = None,
           plan: str = "free", email_verified: bool = True) -> dict:
    """ID token claims as the accounts login trigger would produce them."""
    c = {"sub": user_id, "email": f"{user_id}@example.com", "token_use": "id",
         "email_verified": "true" if email_verified else "false"}
    if tenant_id:
        c.update(tenant_id=tenant_id, role=role, plan=plan)
    return c


def http_event(method: str, path: str, user: dict | None = None,
               body: dict | None = None, query: dict | None = None) -> dict:
    request_context = {
        "http": {"method": method, "path": path, "sourceIp": "127.0.0.1"},
        "requestId": "test",
        "stage": "$default",
    }
    if user is not None:
        request_context["authorizer"] = {"jwt": {"claims": user, "scopes": []}}
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "&".join(f"{k}={v}" for k, v in (query or {}).items()),
        "queryStringParameters": query,
        "headers": {"content-type": "application/json"},
        "requestContext": request_context,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def call(handler, method: str, path: str, user: dict | None = None,
         body: dict | None = None, query: dict | None = None):
    """Returns (status_code, parsed_json_or_None)."""
    response = handler(http_event(method, path, user, body, query), FakeContext())
    raw = response.get("body")
    return response["statusCode"], (json.loads(raw) if raw else None)
