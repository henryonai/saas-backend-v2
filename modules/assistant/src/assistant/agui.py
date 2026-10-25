"""
Turns the agent's stream (LangGraph, stream_mode="messages") into AG-UI events
(https://docs.ag-ui.com/concepts/events), in the order AG-UI clients check:

  TEXT_MESSAGE_START, _CONTENT..., _END   the answer, as the model writes it
  TOOL_CALL_START, _ARGS..., _END         a tool the model calls (an open text message ends first)
  TOOL_CALL_RESULT                        what the tool returned

The tool calls of one model call share its message id (their parentMessageId),
so a client shows them on that assistant message. Only the calling agent's
model streams: a subagent runs inside the `task` tool, and its answer arrives
as that tool's result.

Transcript assembles the same events into AG-UI messages, as a client does:
the conversation's history, which the page shows again when it is reopened.
"""
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import uuid4

from ag_ui.core import (
    BaseEvent,
    RunErrorEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage

from shared.errors import AppError

from assistant.schemas import ERROR_CODES


def error_event(error: AppError) -> RunErrorEvent:
    return RunErrorEvent(code=ERROR_CODES.get(error.status_code, "VALIDATION_ERROR"), message=error.message)


def internal_error() -> RunErrorEvent:
    return RunErrorEvent(code="INTERNAL_ERROR", message="The assistant could not answer. Please try again.")


@dataclass
class AgUiStream:
    model_call: str | None = None   # message id of the model call streaming now
    text: str | None = None         # the open text message
    texts: int = 0                  # text messages started by this model call
    calls: dict[object, str] = field(default_factory=dict)   # this call's tool calls: chunk index -> id
    open: set[str] = field(default_factory=set)              # tool calls whose arguments still stream
    awaiting_result: set[str] = field(default_factory=set)   # ended tool calls, until their result

    def message(self, message: BaseMessage) -> Iterator[BaseEvent]:
        if isinstance(message, AIMessage):
            yield from self._ai(message)
        elif isinstance(message, ToolMessage):
            yield from self._tool_result(message)

    def close(self) -> Iterator[BaseEvent]:
        """Ends the model call's open text and tool calls: when the next message starts, or the stream ends."""
        yield from self._end_text()
        for call_id in sorted(self.open):
            yield ToolCallEndEvent(tool_call_id=call_id)
        self.awaiting_result |= self.open
        self.model_call, self.texts, self.calls, self.open = None, 0, {}, set()

    def _ai(self, message: AIMessage) -> Iterator[BaseEvent]:
        message_id = message.id or str(uuid4())   # a model call's chunks all carry its id
        if message_id != self.model_call:
            yield from self.close()
            self.model_call = message_id
        if message.text:
            if self.text is None:
                # Text after this call's tool calls (rare) is a new message: AG-UI ids are unique.
                self.text = message_id if self.texts == 0 else f"{message_id}-{self.texts}"
                self.texts += 1
                yield TextMessageStartEvent(message_id=self.text, role="assistant")
            yield TextMessageContentEvent(message_id=self.text, delta=message.text)
        if isinstance(message, AIMessageChunk):
            chunks = message.tool_call_chunks
        else:   # a whole message, e.g. from middleware
            chunks = [{"id": c["id"], "name": c["name"], "args": json.dumps(c["args"]), "index": i}
                      for i, c in enumerate(message.tool_calls)]
        for chunk in chunks:
            key = chunk.get("index") if chunk.get("index") is not None else chunk.get("id")
            if key not in self.calls:
                if not (chunk.get("id") and chunk.get("name")):
                    continue   # arguments for a call that never started: nothing to attach them to
                yield from self._end_text()
                self.calls[key] = chunk["id"]
                self.open.add(chunk["id"])
                yield ToolCallStartEvent(tool_call_id=chunk["id"], tool_call_name=chunk["name"],
                                         parent_message_id=message_id)
            if chunk.get("args"):
                yield ToolCallArgsEvent(tool_call_id=self.calls[key], delta=chunk["args"])

    def _tool_result(self, message: ToolMessage) -> Iterator[BaseEvent]:
        yield from self.close()   # tools run after the model call that asked for them
        if message.tool_call_id not in self.awaiting_result:
            return   # e.g. the "interrupted" result for a call from an earlier message
        self.awaiting_result.discard(message.tool_call_id)
        yield ToolCallResultEvent(message_id=message.id or str(uuid4()), tool_call_id=message.tool_call_id,
                                  content=message.text, role="tool")

    def _end_text(self) -> Iterator[BaseEvent]:
        if self.text is not None:
            yield TextMessageEndEvent(message_id=self.text)
            self.text = None


@dataclass
class Transcript:
    """
    The AG-UI messages a run's events add up to, assembled as @ag-ui/client
    does: text and tool calls on the assistant message they belong to (a tool
    call's parentMessageId), each tool's result as a tool message. JSON as on
    the wire, so the page reads it back as it read the stream.
    """
    messages: list[dict] = field(default_factory=list)
    _by_id: dict[str, dict] = field(default_factory=dict)
    _calls: dict[str, dict] = field(default_factory=dict)

    def add(self, event: BaseEvent) -> None:
        match event:
            case TextMessageStartEvent():
                self._assistant(event.message_id).setdefault("content", "")
            case TextMessageContentEvent():
                message = self._assistant(event.message_id)
                message["content"] = message.get("content", "") + event.delta
            case ToolCallStartEvent():
                call = {"id": event.tool_call_id, "type": "function",
                        "function": {"name": event.tool_call_name, "arguments": ""}}
                self._assistant(event.parent_message_id or event.tool_call_id).setdefault("toolCalls", []).append(call)
                self._calls[event.tool_call_id] = call
            case ToolCallArgsEvent():
                self._calls[event.tool_call_id]["function"]["arguments"] += event.delta
            case ToolCallResultEvent():
                self.messages.append({"id": event.message_id, "role": "tool", "toolCallId": event.tool_call_id,
                                      "content": event.content})

    def _assistant(self, message_id: str) -> dict:
        if message_id not in self._by_id:
            self._by_id[message_id] = {"id": message_id, "role": "assistant"}
            self.messages.append(self._by_id[message_id])
        return self._by_id[message_id]
