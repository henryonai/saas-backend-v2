from typing import Protocol
from urllib.parse import quote

import httpx
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from assistant.context import AgentContext


class AppApi(Protocol):
    def get(self, authorization: str, path: str, params: dict | None = None) -> dict: ...


class HttpAppApi:
    def __init__(self, client: httpx.Client, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")

    def get(self, authorization: str, path: str, params: dict | None = None) -> dict:
        try:
            response = self.client.get(f"{self.base_url}{path}", params=params,
                                       headers={"Authorization": authorization})
        except httpx.HTTPError:
            return {"error": "The app's API is not reachable right now."}
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error:
            return {"error": body.get("message") or f"The API answered {response.status_code}."}
        return body


def make_order_tools(api: AppApi) -> list[BaseTool]:
    @tool
    def list_orders(runtime: ToolRuntime[AgentContext], limit: int = 10) -> dict:
        """List the workspace's most recent orders, newest first. `limit` is at most 50."""
        return api.get(runtime.context.authorization, "/orders", {"limit": max(1, min(limit, 50))})

    @tool
    def get_order(order_id: str, runtime: ToolRuntime[AgentContext]) -> dict:
        """Get one order of the workspace by its order_id."""
        return api.get(runtime.context.authorization, f"/orders/{quote(order_id, safe='')}")

    return [list_orders, get_order]
