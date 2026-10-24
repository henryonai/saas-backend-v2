from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from assistant.service import UsageService


class PlanToolsMiddleware(AgentMiddleware):
    """Hides plan-gated tools from the model. The tools check the plan too: a model can call a tool it wasn't shown."""

    def __init__(self, usage: UsageService, sandbox_tools: set[str]):
        super().__init__()
        self.usage = usage
        self.sandbox_tools = sandbox_tools

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        if not self.usage.includes_sandbox(request.runtime.context.user):
            request = request.override(tools=[t for t in request.tools
                                              if getattr(t, "name", None) not in self.sandbox_tools])
        return handler(request)
