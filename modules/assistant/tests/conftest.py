"""
Builds the real agents from agents/*.md (create_agent, middleware, tools,
subagents), the real UsageService, conversation services and titles on a
fake table, the real HTTP API (handler.py) and AgentCore Runtime app (app.py)
around them, with one scripted model per agent, a fake model for titles, an
in-memory checkpointer, and fakes for the HTTP API the tools call and the
sandbox.
"""
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import boto3
import pytest
import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

from assistant.app import create_app
from assistant.container import get_api_file_service, get_conversation_service, get_memory_service
from assistant.definitions import MAIN_AGENT, load_agents
from assistant.factory import AgentDeps, build_agents
from assistant.files import S3FileStore
from assistant.handler import app as http_app
from assistant.handler import handler
from assistant.models import current_month
from assistant.repository import ConversationRepository, FileRepository, MemoryRepository, UsageRepository
from assistant.runs import Runs
from assistant.sandbox import CommandResult
from assistant.service import ConversationLog, ConversationService, FileService, MemoryService, UsageService
from assistant.titles import Titles
from assistant.tools import make_tools
from testkit.http import ONLY_WORKSPACE, call, claims, only_workspace

SESSION = "11111111-2222-3333-4444-555555555555"
FILES_BUCKET = "assistant-files-test"
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class ScriptedModel(BaseChatModel):
    """
    Answers with the given messages, in order, and records what it was sent.
    Streams like Bedrock Converse: text in pieces, tool calls and token usage
    on the last chunk (the agent streams, so this is the path production takes).
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    tools_shown: list[list[str]] = []   # tool names offered to the model, per call
    pause: float = 0.0   # if set: the text a word at a time, this many seconds apart

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
        if self.pause and text:   # a model writing a word at a time
            words = text.split(" ")
            pieces = [word + " " for word in words[:-1]] + [words[-1]]
        else:
            pieces = [text[:len(text) // 2], text[len(text) // 2:]] if text else [""]
        for i, piece in enumerate(pieces):
            if i:
                time.sleep(self.pause)
            last = i == len(pieces) - 1
            calls = [{"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": n}
                     for n, c in enumerate(message.tool_calls)] if last else []
            yield ChatGenerationChunk(message=AIMessageChunk(
                content=piece, tool_call_chunks=calls, usage_metadata=message.usage_metadata if last else None))


class TitleModel(BaseChatModel):
    """
    The model titles.Titles asks for a title: answers `title`, or fails when
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


class FakeApi:
    """Stands in for the HTTP API: records each call and answers from `responses` by path."""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: dict[str, dict] = {}

    def get(self, authorization: str, tenant_id: str, path: str, params: dict | None = None) -> dict:
        self.calls.append({"authorization": authorization, "tenant_id": tenant_id, "path": path, "params": params})
        return self.responses.get(path, {"error": "Not found"})


class FakeSandbox:
    """
    Stands in for Code Interpreter: records commands per conversation and
    answers with `result`; keeps the files written to it, by conversation and path.
    """

    def __init__(self):
        self.started: list[str] = []
        self.commands: list[dict] = []
        self.files: dict[tuple[str, str], bytes] = {}
        self.result = CommandResult(exit_code=0, stdout="", stderr="")

    def run(self, conversation_id: str, command: str, on_new_session) -> CommandResult:
        self._start(conversation_id, on_new_session)
        self.commands.append({"conversation_id": conversation_id, "command": command})
        return self.result

    def write_file(self, conversation_id: str, path: str, data: bytes, on_new_session) -> bool:
        new = self._start(conversation_id, on_new_session)
        self.files[conversation_id, path] = data
        return new

    def read_file(self, conversation_id: str, path: str) -> bytes | None:
        if conversation_id not in self.started:
            return None
        if (conversation_id, path) not in self.files:
            from assistant.sandbox import SandboxError
            raise SandboxError(f"No such file: {path}")
        return self.files[conversation_id, path]

    def _start(self, conversation_id: str, on_new_session) -> bool:
        if conversation_id in self.started:
            return False
        on_new_session()   # may refuse (plan limits): then nothing runs
        self.started.append(conversation_id)
        return True


def id_token(user_claims: dict) -> str:
    """'Bearer <JWT>' carrying these claims. Unsigned: AgentCore verifies signatures, not our code."""
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"Bearer {part({'alg': 'RS256'})}.{part(user_claims)}.signature"


def table_from_template(name: str):
    """
    The assistant table with template.yaml's keys and indexes, so a read from
    an index gets only the fields that index keeps there too.
    """
    class Template(yaml.SafeLoader):
        pass

    Template.add_multi_constructor("!", lambda loader, tag, node: None)   # CloudFormation's tags: not needed here
    template = yaml.load((Path(__file__).resolve().parents[3] / "template.yaml").read_text(), Template)
    spec = template["Resources"]["AssistantTable"]["Properties"]
    boto3.client("dynamodb").create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST", AttributeDefinitions=spec["AttributeDefinitions"],
        KeySchema=spec["KeySchema"], GlobalSecondaryIndexes=spec["GlobalSecondaryIndexes"])
    return boto3.resource("dynamodb").Table(name)


@pytest.fixture
def ctx():
    table = table_from_template("assistant-test")
    usage = UsageService(usage=UsageRepository(table))
    conversations = ConversationRepository(table)
    history = ConversationLog(conversations, days=0)   # kept until deleted, as template.yaml's default
    api, sandbox = FakeApi(), FakeSandbox()
    title_model = TitleModel(calls=[])
    titles = Titles(title_model, history, usage)
    memories = MemoryService(MemoryRepository(table))
    s3 = boto3.client("s3", region_name="ap-southeast-1")
    s3.create_bucket(Bucket=FILES_BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"})
    files = FileService(FileRepository(table), S3FileStore(s3, FILES_BUCKET), usage)
    tools = make_tools(api, usage, sandbox, memories, files)
    definitions = load_agents(set(tools))
    models: dict[str, ScriptedModel] = {}
    state = SimpleNamespace(agents=None, checkpointer=InMemorySaver())

    def script(*responses: AIMessage, agent: str = MAIN_AGENT, pause: float = 0.0):
        """Sets what `agent`'s model answers next; conversations (the checkpointer) are kept."""
        models[agent] = ScriptedModel(responses=list(responses), calls=[], tools_shown=[], pause=pause)
        state.agents = build_agents(definitions, AgentDeps(
            make_model=lambda d: models.setdefault(d.name, ScriptedModel(responses=[], calls=[], tools_shown=[])),
            checkpointer=state.checkpointer, usage=usage, tools=tools, memories=memories, files=files))

    # The HTTP API (handler.py) with this table, and the AgentCore Runtime app (app.py) with these agents.
    purges: list[list[str]] = []
    service = ConversationService(conversations, usage=usage, days=0, purge=SimpleNamespace(send=purges.append),
                                  files=files)
    http_app.dependency_overrides[get_conversation_service] = lambda: service
    http_app.dependency_overrides[get_memory_service] = lambda: memories
    http_app.dependency_overrides[get_api_file_service] = lambda: files
    runs = Runs()

    def send(prompt: str, user: dict | None, thread_id: str = SESSION, agent: str | None = None,
             message_id: str | None = None, replaces: str | None = None,
             attachments: list[str] | None = None) -> tuple[int, dict | None]:
        """What the page does first: POST /assistant/conversations/{threadId}/messages. (status, body)"""
        body = {"id": message_id or str(uuid4()), "content": prompt, **({"agent": agent} if agent else {}),
                **({"replaces": replaces} if replaces else {}), **({"attachments": attachments} if attachments else {})}
        return call(handler, "POST", f"/assistant/conversations/{thread_id}/messages", user, body=body)

    def run(run_id: str, user: dict | None = None, thread_id: str = SESSION, session_id: str | None = SESSION,
            authorization: str | None = None, messages: list | None = None,
            resume: dict | None = None, tenant_id: str | None | object = ONLY_WORKSPACE) -> list[dict]:
        """
        What the page does next, and again to follow the answer after a
        reload (or, with `resume`, to read on after its stream broke): sends
        AgentCore the run, in `tenant_id` (forwardedProps.tenantId; by default
        the token's only workspace). Every event as it went on the wire, after
        checking their order.
        """
        tenant = only_workspace(user) if tenant_id is ONLY_WORKSPACE else tenant_id
        headers = {"Authorization": authorization if authorization is not None else id_token(user)}
        if session_id:
            headers[SESSION_HEADER] = session_id
        response = runtime.post("/invocations", headers=headers, json={
            "threadId": thread_id, "runId": run_id, "state": {}, "tools": [], "context": [],
            "forwardedProps": {**({"tenantId": tenant} if tenant else {}), **({"resume": resume} if resume else {})},
            "messages": messages or []})
        assert response.status_code == 200, response.text
        events = sse_events(response)
        check_agui_order(events, resumed=resume is not None)
        return events

    def post(prompt: str, user: dict, thread_id: str = SESSION, agent: str | None = None,
             message_id: str | None = None, replaces: str | None = None, attachments: list[str] | None = None) -> str:
        """send() that must succeed: the run's id."""
        status, body = send(prompt, user, thread_id, agent, message_id, replaces, attachments)
        assert status == 201, body
        return body["run_id"]

    def chat(prompt: str, user: dict, agent: str | None = None, thread_id: str = SESSION) -> list[dict]:
        """Sends one message as `user` (claims), as the page does: saved, then run. Every event of the answer."""
        return run(post(prompt, user, thread_id, agent), user, thread_id=thread_id, session_id=thread_id)

    def set_usage(tenant_id: str, **counters):
        table.put_item(Item={"pk": f"TENANT#{tenant_id}", "sk": f"USAGE#{current_month()}",
                             "tenant_id": tenant_id, "month": current_month(), **counters})

    script()
    # One event loop for every request, as uvicorn has: a run outlives the request that started it.
    with TestClient(create_app(lambda: state.agents, lambda: history, lambda: titles, runs)) as runtime:
        yield SimpleNamespace(table=table, usage=usage, api=api, sandbox=sandbox, state=state, memories=memories,
                              files=files, s3=s3,
                              conversations=conversations, history=history, service=service,
                              title_model=title_model,
                              definitions=definitions, script=script, send=send, post=post, run=run, chat=chat,
                              runs=runs,
                              runtime=runtime, purges=purges, set_usage=set_usage, session=SESSION,
                              answer=answer, tool_call=tool_call, text=text_of, tools=tools_called, error=error_of,
                              check_order=check_agui_order, id_token=id_token,
                              model_calls=lambda agent=MAIN_AGENT: models[agent].calls,
                              tools_shown=lambda agent=MAIN_AGENT: models[agent].tools_shown)
    http_app.dependency_overrides.clear()


@pytest.fixture
def alice():
    """A member of tenant t1 on the Free plan."""
    return claims("alice", "t1", "member", plan="free")


def sse_events(response) -> list[dict]:
    """The AG-UI events of an SSE response: the JSON of each `data:` line."""
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


def text_of(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def tools_called(events: list[dict]) -> list[str]:
    return [e["toolCallName"] for e in events if e["type"] == "TOOL_CALL_START"]


def error_of(events: list[dict]) -> dict | None:
    """The run's RUN_ERROR (its code and message), if it failed."""
    return next(({"code": e.get("code"), "message": e["message"]} for e in events if e["type"] == "RUN_ERROR"), None)


def check_agui_order(events: list[dict], resumed: bool = False) -> None:
    """
    The order AG-UI clients enforce (@ag-ui/client verifies every stream).
    A `resumed` stream may give a result for a tool call that ended before it.
    """
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
                assert (e["toolCallId"] in ended or resumed and e["toolCallId"] not in open_calls) \
                    and e["role"] == "tool", e
                ended.discard(e["toolCallId"])
            case "RUN_FINISHED":
                assert text is None and not open_calls, e
