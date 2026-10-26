"""
Stopping a run's model call when the user presses Stop (app.handle), and
counting what that call cost.

Closing the agent's stream waits for the step that is running, and a model
streaming a long answer would go on to its end: tokens nobody reads, and the
conversation busy (409) meanwhile. StopModel, a callback on every model call
of the run, raises Stopped from the call's next token, which ends its stream.

A stopped call never gets the provider's token counts: they come with its
last chunk. So StopModel estimates them, as ConversationMiddleware measures
history (about 4 characters a token): what the model was sent, tool
definitions included, and what it wrote. UsageMiddleware counts them against
the plan like any call's, or a user could stop every answer just before its
end and use the model for free; app.py adds them to the run's tokens.
"""
import math
import threading
from uuid import UUID

from aws_lambda_powertools import Logger
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages.utils import count_tokens_approximately

logger = Logger()

CHARS_PER_TOKEN = 4   # as count_tokens_approximately


class Stopped(Exception):
    """The user pressed Stop: ends the model call the run is in. Carries that call's tokens, estimated."""

    def __init__(self, input_tokens: int, output_tokens: int):
        super().__init__("The user stopped the run")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class StopModel(BaseCallbackHandler):
    """One per run: ends each of its model calls at their next token once `stopping` is set."""
    raise_error = True

    def __init__(self):
        self.stopping = threading.Event()
        self.input_tokens = self.output_tokens = 0   # of the calls it stopped, estimated
        self._calls: dict[UUID, list[int]] = {}       # each call under way: [its prompt's tokens, characters written]
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs) -> None:
        self._calls[run_id] = [_prompt_tokens(messages, kwargs.get("invocation_params")), 0]

    def on_llm_new_token(self, token: str, *, chunk=None, run_id: UUID, **kwargs) -> None:
        call = self._calls.setdefault(run_id, [0, 0])
        call[1] += len(token) + _tool_call_characters(chunk)
        if self.stopping.is_set():
            del self._calls[run_id]
            stopped = Stopped(call[0], math.ceil(call[1] / CHARS_PER_TOKEN))
            with self._lock:
                self.input_tokens += stopped.input_tokens
                self.output_tokens += stopped.output_tokens
            raise stopped

    def on_llm_end(self, response, *, run_id: UUID, **kwargs) -> None:
        self._calls.pop(run_id, None)

    def on_llm_error(self, error, *, run_id: UUID, **kwargs) -> None:
        self._calls.pop(run_id, None)


def _prompt_tokens(messages, invocation_params: dict | None) -> int:
    """A call's prompt, estimated. Never raises: raise_error would make it fail the model call."""
    try:
        return count_tokens_approximately(messages[0], tools=(invocation_params or {}).get("tools"))
    except Exception:
        logger.warning("Could not estimate a model call's prompt; a Stop counts only what it wrote", exc_info=True)
        return 0


def _tool_call_characters(chunk) -> int:
    """What a chunk adds to the tool calls being written: their names and arguments stream with no token text."""
    calls = getattr(getattr(chunk, "message", None), "tool_call_chunks", None) or []
    return sum(len(call.get("name") or "") + len(call.get("args") or "") for call in calls)
