from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from assistant.context import AgentContext
from assistant.service import UsageService
from assistant.stop import Stopped

ALLOWANCE_USED = ("I have to stop here: your workspace has used this month's assistant allowance. "
                  "An owner can upgrade the plan for more.")


class UsageState(AgentState):
    within_allowance: NotRequired[bool]


class UsageMiddleware(AgentMiddleware):
    """
    Counts every model call's tokens against the workspace's monthly cap, and
    stops the agent cleanly once it is reached. The cheap checks (who is
    calling, any messages left?) run before the agent, in app.py. A call the
    user stopped counts too, as estimated (stop.py).
    """
    state_schema = UsageState

    def __init__(self, usage: UsageService):
        super().__init__()
        self.usage = usage

    def before_agent(self, state: UsageState, runtime: Runtime[AgentContext]) -> dict:
        # app.py (or the task tool, for a subagent) let this run start under the cap.
        return {"within_allowance": True}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: UsageState, runtime: Runtime[AgentContext]) -> dict | None:
        # Stopping before the next call, not after this one, keeps the history
        # valid: the tool calls the last answer asked for have all been answered.
        if not state.get("within_allowance", True):
            return {"messages": [AIMessage(ALLOWANCE_USED)], "jump_to": "end"}
        return None

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | ExtendedModelResponse:
        # Read from the model's own response, not from the state after the call:
        # other middleware's after_model hooks may append messages first.
        try:
            response = handler(request)
        except Stopped as stopped:
            self.usage.record_tokens(request.runtime.context.user, stopped.input_tokens, stopped.output_tokens)
            raise
        usage = next((m.usage_metadata for m in response.result
                      if isinstance(m, AIMessage) and m.usage_metadata), None)
        if not usage:
            return response
        within = self.usage.record_tokens(request.runtime.context.user,
                                          usage["input_tokens"], usage["output_tokens"])
        return ExtendedModelResponse(model_response=response, command=Command(update={"within_allowance": within}))
