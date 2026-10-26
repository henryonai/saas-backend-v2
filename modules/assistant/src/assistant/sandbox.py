"""
Shell commands in AgentCore Code Interpreter: the assistant's terminal,
behind a small interface so tests can use a fake.

The sandbox is a separate microVM per conversation, so the model's commands
never run where the agent's AWS credentials and the user's token are. It has
no AWS role, and in SANDBOX network mode (template.yaml) no internet. Files
persist between commands until the sandbox session ends (session_seconds
after it started); the next command then gets a new, empty sandbox, and the
result says so.

Sessions are kept per conversation in this process. AgentCore Runtime gives
each conversation its own microVM, and when that stops, its sandbox has timed
out too.
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from botocore.exceptions import ClientError


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    restarted: bool = False  # the previous sandbox had ended: its files are gone


class Sandbox(Protocol):
    def run(self, conversation_id: str, command: str, on_new_session: Callable[[], None]) -> CommandResult:
        """Runs `command` in the conversation's sandbox, starting one if needed.
        on_new_session runs first and may raise to refuse (plan limits)."""
        ...

    def write_file(self, conversation_id: str, path: str, data: bytes, on_new_session: Callable[[], None]) -> bool:
        """Puts a file in the conversation's sandbox, starting one if needed. True if that one is new (empty)."""
        ...

    def read_file(self, conversation_id: str, path: str) -> bytes | None:
        """A file from the conversation's sandbox; None if it has none running (its files are gone)."""
        ...


class SandboxError(Exception):
    """The sandbox service failed (throttled, internal error...)."""


class _SessionGone(Exception):
    pass


class AgentCoreSandbox:
    def __init__(self, client, interpreter_id: str, session_seconds: int):
        self.client = client  # boto3 "bedrock-agentcore" (data plane)
        self.interpreter_id = interpreter_id
        self.session_seconds = session_seconds
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    def run(self, conversation_id: str, command: str, on_new_session: Callable[[], None]) -> CommandResult:
        session_id = self._session(conversation_id, on_new_session)
        try:
            return self._execute(session_id, command)
        except _SessionGone:
            with self._lock:
                self._sessions.pop(conversation_id, None)
            result = self._execute(self._session(conversation_id, on_new_session), command)
            return CommandResult(result.exit_code, result.stdout, result.stderr, restarted=True)

    def write_file(self, conversation_id: str, path: str, data: bytes, on_new_session: Callable[[], None]) -> bool:
        with self._lock:
            new = conversation_id not in self._sessions
        arguments = {"content": [{"path": path, "blob": data}]}
        try:
            self._invoke(self._session(conversation_id, on_new_session), "writeFiles", arguments)
        except _SessionGone:
            with self._lock:
                self._sessions.pop(conversation_id, None)
            self._invoke(self._session(conversation_id, on_new_session), "writeFiles", arguments)
            return True
        return new

    def read_file(self, conversation_id: str, path: str) -> bytes | None:
        with self._lock:
            session_id = self._sessions.get(conversation_id)
        if session_id is None:
            return None
        try:
            results = self._invoke(session_id, "readFiles", {"paths": [path]})
        except _SessionGone:
            with self._lock:
                self._sessions.pop(conversation_id, None)
            return None
        for result in results:
            if result.get("isError"):
                raise SandboxError(" ".join(c.get("text", "") for c in result.get("content", [])) or f"No file {path}")
            for content in result.get("content", []):
                resource = content.get("resource") or {}
                if resource.get("blob") is not None:
                    return resource["blob"]
                if resource.get("text") is not None:
                    return resource["text"].encode()
        raise SandboxError(f"No file {path} in the sandbox.")

    def _session(self, conversation_id: str, on_new_session: Callable[[], None]) -> str:
        with self._lock:
            if conversation_id not in self._sessions:
                on_new_session()
                response = self.client.start_code_interpreter_session(
                    codeInterpreterIdentifier=self.interpreter_id,
                    name=f"conversation-{conversation_id[:40]}",
                    sessionTimeoutSeconds=self.session_seconds,
                )
                self._sessions[conversation_id] = response["sessionId"]
            return self._sessions[conversation_id]

    def _invoke(self, session_id: str, name: str, arguments: dict) -> list[dict]:
        """One of the sandbox's tools (executeCommand, readFiles, writeFiles): the results it streamed."""
        try:
            response = self.client.invoke_code_interpreter(
                codeInterpreterIdentifier=self.interpreter_id, sessionId=session_id, name=name, arguments=arguments)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise _SessionGone from e
            raise SandboxError(e.response["Error"].get("Message", "The sandbox failed")) from e
        results = []
        for event in response["stream"]:
            if "resourceNotFoundException" in event:
                raise _SessionGone
            if errors := [v for k, v in event.items() if k.endswith("Exception")]:
                raise SandboxError(errors[0].get("message", "The sandbox failed"))
            results.append(event.get("result") or {})
        return results

    def _execute(self, session_id: str, command: str) -> CommandResult:
        stdout, stderr, exit_code = [], [], None
        for result in self._invoke(session_id, "executeCommand", {"command": command}):
            if structured := result.get("structuredContent"):
                stdout.append(structured.get("stdout") or "")
                stderr.append(structured.get("stderr") or "")
                exit_code = structured.get("exitCode", exit_code)
            else:  # no structured output: the text content blocks are the output
                stdout += [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
                if result.get("isError") and exit_code is None:
                    exit_code = 1
        return CommandResult(exit_code=exit_code, stdout="".join(stdout), stderr="".join(stderr))
