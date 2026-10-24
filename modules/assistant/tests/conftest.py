"""
Builds the real agents from agents/*.md (create_agent, middleware, tools,
subagents), the real UsageService and ConversationLog on a fake table, with
one scripted model per agent, a fake model for titles, an in-memory
checkpointer, and fakes for AgentCore Memory, the HTTP API and the sandbox.
"""
import base64
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from bedrock_agentcore import RequestContext
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from assistant.app import handle
from assistant.definitions import MAIN_AGENT, load_agents
from assistant.factory import AgentDeps, build_agents
from assistant.models import current_month
from assistant.repository import ConversationRepository, UsageRepository
from assistant.sandbox import CommandResult
from assistant.service import ConversationLog, UsageService
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


class TitleModel(BaseChatModel):
    """
    The model TitleMiddleware asks for a title: answers `title`, or fails when
    `fail` is set. Reports token usage only when given `tokens`, so other
    tests' token counts are the agent's alone.
    """

    title: str = "Test conversation"
    fail: bool = False
    tokens: tuple[int, int] | None = None
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "title"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("the model is down")
        usage = None
        if self.tokens:
            usage = {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1], "total_tokens": sum(self.tokens)}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.title, usage_metadata=usage))])


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
    table = create_table("assistant-test", with_gsi1=True)
    usage = UsageService(usage=UsageRepository(table))
    conversations = ConversationRepository(table)
    history = ConversationLog(conversations, days=30)
    memory, api, sandbox = FakeMemory(), FakeApi(), FakeSandbox()
    title_model = TitleModel(calls=[])
    tools = make_tools(api, usage, sandbox)
    definitions = load_agents(set(tools))
    models: dict[str, ScriptedModel] = {}
    state = SimpleNamespace(agents=None, checkpointer=InMemorySaver())

    def script(*responses: AIMessage, agent: str = MAIN_AGENT):
        """Sets what `agent`'s model answers next; conversations (the checkpointer) are kept."""
        models[agent] = ScriptedModel(responses=list(responses), calls=[], tools_shown=[])
        state.agents = build_agents(definitions, AgentDeps(
            make_model=lambda d: models.setdefault(d.name, ScriptedModel(responses=[], calls=[], tools_shown=[])),
            checkpointer=state.checkpointer, usage=usage, memory=memory, tools=tools, history=history,
            title_model=title_model))

    def chat(prompt, user: dict | None = None, session_id: str | None = SESSION, authorization=None,
             agent: str | None = None, thread_id: str | None = None, messages: list | None = None) -> list[dict]:
        """
        Sends one message as `user` (claims), as an AG-UI client does, and
        returns every event as it goes on the wire, after checking their order.
        """
        headers = {"Authorization": authorization if authorization is not None else id_token(user)}
        context = RequestContext(session_id=session_id, request_headers=headers)
        run_input = RunAgentInput(
            thread_id=thread_id or session_id or SESSION, run_id=str(uuid4()),
            messages=messages if messages is not None else [{"id": str(uuid4()), "role": "user", "content": prompt}],
            forwarded_props={"agent": agent} if agent else {})
        events = [wire(event) for event in handle(run_input, context, usage, state.agents, history)]
        check_agui_order(events)
        return events

    def set_usage(tenant_id: str, **counters):
        table.put_item(Item={"pk": f"TENANT#{tenant_id}", "sk": f"USAGE#{current_month()}",
                             "tenant_id": tenant_id, "month": current_month(), **counters})

    script()
    yield SimpleNamespace(table=table, usage=usage, memory=memory, api=api, sandbox=sandbox, state=state,
                          conversations=conversations, history=history, title_model=title_model,
                          definitions=definitions, script=script, chat=chat, set_usage=set_usage, session=SESSION,
                          answer=answer, tool_call=tool_call, text=text_of, tools=tools_called, error=error_of,
                          check_order=check_agui_order, id_token=id_token,
                          model_calls=lambda agent=MAIN_AGENT: models[agent].calls,
                          tools_shown=lambda agent=MAIN_AGENT: models[agent].tools_shown)


@pytest.fixture
def alice():
    """A member of tenant t1 on the Free plan."""
    return claims("alice", "t1", "member", plan="free")


def wire(event) -> dict:
    """An AG-UI event as the client receives it: the JSON of one SSE `data:` line."""
    return json.loads(EventEncoder().encode(event).removeprefix("data: "))


def text_of(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def tools_called(events: list[dict]) -> list[str]:
    return [e["toolCallName"] for e in events if e["type"] == "TOOL_CALL_START"]


def error_of(events: list[dict]) -> dict | None:
    """The run's RUN_ERROR (its code and message), if it failed."""
    return next(({"code": e.get("code"), "message": e["message"]} for e in events if e["type"] == "RUN_ERROR"), None)


def check_agui_order(events: list[dict]) -> None:
    """The order AG-UI clients enforce (@ag-ui/client verifies every stream)."""
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED" and types.count("RUN_STARTED") == 1, types
    assert types[-1] in ("RUN_FINISHED", "RUN_ERROR"), types
    assert types.count("RUN_FINISHED") + types.count("RUN_ERROR") == 1, types
    text, open_calls, ended, seen = None, set(), set(), set()
    for e in events:
        match e["type"]:
            case "TEXT_MESSAGE_START":
                assert text is None and not open_calls and e["messageId"] not in seen, e
                text = e["messageId"]
                seen.add(text)
            case "TEXT_MESSAGE_CONTENT":
                assert e["messageId"] == text and e["delta"], e
            case "TEXT_MESSAGE_END":
                assert e["messageId"] == text, e
                text = None
            case "TOOL_CALL_START":
                assert text is None and e["toolCallId"] not in open_calls | ended, e
                open_calls.add(e["toolCallId"])
            case "TOOL_CALL_ARGS":
                assert e["toolCallId"] in open_calls, e
            case "TOOL_CALL_END":
                open_calls.remove(e["toolCallId"])
                ended.add(e["toolCallId"])
            case "TOOL_CALL_RESULT":
                assert e["toolCallId"] in ended and e["role"] == "tool", e
                ended.discard(e["toolCallId"])
            case "RUN_FINISHED":
                assert text is None and not open_calls, e
