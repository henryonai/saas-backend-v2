"""
AgentCoreSandbox against a fake bedrock-agentcore client that checks every
request against the real API model (botocore), so a wrong parameter fails here.
"""
import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

from assistant.sandbox import AgentCoreSandbox, SandboxError

INTERPRETER = "saas_dev_sandbox-abcdefghij"
API = boto3.client("bedrock-agentcore", region_name="ap-southeast-1").meta.service_model


class FakeAgentCore:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.streams: list = []   # what each invoke returns: a list of events, or an exception
        self.sessions = 0

    def _record(self, operation: str, params: dict):
        validate_parameters(params, API.operation_model(operation).input_shape)
        self.calls.append((operation, params))

    def start_code_interpreter_session(self, **params):
        self._record("StartCodeInterpreterSession", params)
        self.sessions += 1
        return {"codeInterpreterIdentifier": INTERPRETER, "sessionId": f"session-{self.sessions}"}

    def invoke_code_interpreter(self, **params):
        self._record("InvokeCodeInterpreter", params)
        answer = self.streams.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"sessionId": params["sessionId"], "stream": iter(answer)}


def output(stdout="", stderr="", exit_code=0):
    return [{"result": {"content": [{"type": "text", "text": stdout}],
                        "structuredContent": {"stdout": stdout, "stderr": stderr, "exitCode": exit_code}}}]


def not_found():
    return ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "Session not found"}},
                       "InvokeCodeInterpreter")


@pytest.fixture
def sandbox():
    client = FakeAgentCore()
    return AgentCoreSandbox(client, INTERPRETER, session_seconds=900), client


def test_runs_commands_in_one_session_per_conversation(sandbox):
    box, client = sandbox
    client.streams = [output("hello\n"), output("", "oops", 2), output("other\n")]
    started = []

    first = box.run("conv-a", "echo hello", on_new_session=lambda: started.append("a"))
    second = box.run("conv-a", "false", on_new_session=lambda: started.append("a again"))
    box.run("conv-b", "echo other", on_new_session=lambda: started.append("b"))

    assert (first.exit_code, first.stdout) == (0, "hello\n")      # stdout once, not also from the text block
    assert (second.exit_code, second.stderr) == (2, "oops")
    assert started == ["a", "b"]
    invokes = [p for op, p in client.calls if op == "InvokeCodeInterpreter"]
    assert [p["sessionId"] for p in invokes] == ["session-1", "session-1", "session-2"]
    assert invokes[0]["name"] == "executeCommand" and invokes[0]["arguments"] == {"command": "echo hello"}


def test_an_ended_session_is_replaced_and_reported(sandbox):
    box, client = sandbox
    client.streams = [output("1\n"), not_found(), output("2\n")]
    counted = []
    box.run("conv-a", "echo 1", on_new_session=lambda: counted.append(1))

    result = box.run("conv-a", "echo 2", on_new_session=lambda: counted.append(1))

    assert result.restarted and result.stdout == "2\n"
    assert client.sessions == 2 and len(counted) == 2   # the new sandbox counts against the plan too


def test_a_refused_session_runs_nothing(sandbox):
    box, client = sandbox

    def refuse():
        raise RuntimeError("plan limit")

    with pytest.raises(RuntimeError):
        box.run("conv-a", "ls", on_new_session=refuse)
    assert client.calls == []


def test_service_errors_raise(sandbox):
    box, client = sandbox
    client.streams = [[{"throttlingException": {"message": "Slow down"}}],
                      ClientError({"Error": {"Code": "InternalServerException", "Message": "Boom"}},
                                  "InvokeCodeInterpreter")]
    with pytest.raises(SandboxError, match="Slow down"):
        box.run("conv-a", "ls", on_new_session=lambda: None)
    with pytest.raises(SandboxError, match="Boom"):
        box.run("conv-a", "ls", on_new_session=lambda: None)
