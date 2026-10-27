from typing import Protocol
from urllib.parse import quote

import httpx
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from assistant.context import AgentContext


class AppApi(Protocol):
    def get(self, authorization: str, tenant_id: str, path: str, params: dict | None = None) -> dict: ...


class HttpAppApi:
    def __init__(self, client: httpx.Client, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")

    def get(self, authorization: str, tenant_id: str, path: str, params: dict | None = None) -> dict:
        """As the caller (their own token), in the workspace this conversation is in."""
        try:
            response = self.client.get(f"{self.base_url}{path}", params=params,
                                       headers={"Authorization": authorization, "X-Tenant-Id": tenant_id})
        except httpx.HTTPError:
            return {"error": "The app's API is not reachable right now."}
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error:
            return {"error": body.get("message") or f"The API answered {response.status_code}."}
        return body


# What the orders API's fields mean, as the web app shows them (Unit price, Total).
# Without it, models read price_cents as the order's total.
MONEY = "Money is in cents: price_cents is the price of one unit, so an order's total is price_cents × quantity."


def make_order_tools(api: AppApi) -> list[BaseTool]:
    @tool(description=f"List the workspace's most recent orders, newest first. `limit` is at most 50. {MONEY}")
    def list_orders(runtime: ToolRuntime[AgentContext], limit: int = 10) -> dict:
        return api.get(runtime.context.authorization, runtime.context.user.tenant_id, "/orders",
                       {"limit": max(1, min(limit, 50))})

    @tool(description=f"Get one order of the workspace by its order_id. {MONEY}")
    def get_order(order_id: str, runtime: ToolRuntime[AgentContext]) -> dict:
        return api.get(runtime.context.authorization, runtime.context.user.tenant_id,
                       f"/orders/{quote(order_id, safe='')}")

    return [list_orders, get_order]
