"""
Builds the real agents from agents/*.md (create_agent, middleware, tools,
subagents) and the real UsageService on a fake table, with one scripted model
per agent, an in-memory checkpointer, and fakes for AgentCore Memory, the
HTTP API and the sandbox.
"""
import base64
import json
from types import SimpleNamespace

import pytest
from bedrock_agentcore import RequestContext
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from assistant.app import handle
from assistant.definitions import MAIN_AGENT, load_agents
from assistant.factory import AgentDeps, build_agents
from assistant.models import current_month
from assistant.repository import UsageRepository
from assistant.sandbox import CommandResult
from assistant.service import UsageService
from assistant.tools import make_tools
from testkit.aws import create_table
from testkit.http import claims

SESSION = "11111111-2222-3333-4444-555555555555"


class ScriptedModel(BaseChatModel):
    """
    Answers with the given messages, in order, and records what it was sent.
    Streams like Bedrock Converse: text in pieces, tool calls and token usage
    on the last chunk (the agent streams, so this is the path production takes).
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    tools_shown: list[list[str]] = []   # tool names offered to the model, per call

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.tools_shown.append([getattr(t, "name", None) or t.get("name") for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        message = self.responses.pop(0)
        text = message.content
        pieces = [text[:len(text) // 2], text[len(text) // 2:]] if text else [""]
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            calls = [{"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": n}
                     for n, c in enumerate(message.tool_calls)] if last else []
            yield ChatGenerationChunk(message=AIMessageChunk(
                content=piece, tool_call_chunks=calls, usage_metadata=message.usage_metadata if last else None))


def answer(text: str, input_tokens: int = 100, output_tokens: int = 20) -> AIMessage:
    return AIMessage(text, usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                                           "total_tokens": input_tokens + output_tokens})


def tool_call(name: str, args: dict, call_id: str = "call-1", input_tokens: int = 100) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}],
                     usage_metadata={"input_tokens": input_tokens, "output_tokens": 10,
                                     "total_tokens": input_tokens + 10})


class FakeMemory:
    """Stands in for AgentCore Memory: returns preset memories and records saved exchanges."""

    def __init__(self):
        self.memories: dict[str, list[str]] = {}
        self.queries: list[tuple[str, str]] = []
        self.saved: list[dict] = []

    def recall(self, actor_id: str, query: str) -> list[str]:
        self.queries.append((actor_id, query))
        return self.memories.get(actor_id, [])

    def remember(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        self.saved.append({"actor_id": actor_id, "session_id": session_id,
                           "user": user_text, "assistant": assistant_text})


class FakeApi:
    """Stands in for the HTTP API: records each call and answers from `responses` by path."""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: dict[str, dict] = {}

    def get(self, authorization: str, path: str, params: dict | None = None) -> dict:
        self.calls.append({"authorization": authorization, "path": path, "params": params})
        return self.responses.get(path, {"error": "Not found"})


class FakeSandbox:
    """Stands in for Code Interpreter: records commands per conversation and answers with `result`."""

    def __init__(self):
        self.started: list[str] = []
        self.commands: list[dict] = []
        self.result = CommandResult(exit_code=0, stdout="", stderr="")

    def run(self, conversation_id: str, command: str, on_new_session) -> CommandResult:
        if conversation_id not in self.started:
            on_new_session()   # may refuse (plan limits): then nothing runs
            self.started.append(conversation_id)
        self.commands.append({"conversation_id": conversation_id, "command": command})
        return self.result


def id_token(user_claims: dict) -> str:
    """'Bearer <JWT>' carrying these claims. Unsigned: AgentCore verifies signatures, not our code."""
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"Bearer {part({'alg': 'RS256'})}.{part(user_claims)}.signature"


@pytest.fixture
def ctx():
    table = create_table("assistant-test")
    usage = UsageService(usage=UsageRepository(table))
    memory, api, sandbox = FakeMemory(), FakeApi(), FakeSandbox()
    tools = make_tools(api, usage, sandbox)
    definitions = load_agents(set(tools))
    models: dict[str, ScriptedModel] = {}
    state = SimpleNamespace(agents=None, checkpointer=InMemorySaver())

    def script(*responses: AIMessage, agent: str = MAIN_AGENT):
        """Sets what `agent`'s model answers next; conversations (the checkpointer) are kept."""
        models[agent] = ScriptedModel(responses=list(responses), calls=[], tools_shown=[])
        state.agents = build_agents(definitions, AgentDeps(
            make_model=lambda d: models.setdefault(d.name, ScriptedModel(responses=[], calls=[], tools_shown=[])),
            checkpointer=state.checkpointer, usage=usage, memory=memory, tools=tools))

    def chat(prompt, user: dict | None = None, session_id: str | None = SESSION, authorization=None,
             agent: str | None = None) -> list[dict]:
        """Sends one message as `user` (claims) and returns every streamed event."""
        headers = {"Authorization": authorization if authorization is not None else id_token(user)}
        context = RequestContext(session_id=session_id, request_headers=headers)
        payload = {"prompt": prompt} if isinstance(prompt, str) else prompt
        if agent:
            payload["agent"] = agent
        return list(handle(payload, context, usage, state.agents))

    def set_usage(tenant_id: str, **counters):
        table.put_item(Item={"pk": f"TENANT#{tenant_id}", "sk": f"USAGE#{current_month()}",
                             "tenant_id": tenant_id, "month": current_month(), **counters})

    script()
    yield SimpleNamespace(table=table, usage=usage, memory=memory, api=api, sandbox=sandbox, state=state,
                          definitions=definitions, script=script, chat=chat, set_usage=set_usage, session=SESSION,
                          answer=answer, tool_call=tool_call, text=text_of, id_token=id_token,
                          model_calls=lambda agent=MAIN_AGENT: models[agent].calls,
                          tools_shown=lambda agent=MAIN_AGENT: models[agent].tools_shown)


@pytest.fixture
def alice():
    """A member of tenant t1 on the Free plan."""
    return claims("alice", "t1", "member", plan="free")


def text_of(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "text")
