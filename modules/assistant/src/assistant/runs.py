"""
The runs this microVM is answering, and what each has said so far.

A run goes on whatever happens to the request that started it: it runs in a
worker thread and writes its AG-UI events to a log, and every request for it,
the first or one that reattached after a reload, reads that log from the
start, then follows it live. Its messages keep their ids, so a page that
reattaches draws the same messages as the one that started it.

AgentCore gives each session (a conversation) a microVM of its own and sends
all of the session's requests there, so a run and all its readers are in this
process. If the microVM goes, its runs go with it: the conversation's run in
DynamoDB says one is still due, and the next request for it starts it again.
"""
import asyncio
import contextvars
import time
from collections.abc import AsyncIterator, Callable, Iterator

from aws_lambda_powertools import Logger

logger = Logger()

KEEP_ENDED_SECONDS = 600   # a reattach that races the end still finds the whole run

_END = object()


class RunLog:
    """
    One run's events, for whoever reads them. Changed only on the event
    loop: a reader takes the backlog and its place in line in one step, so it
    misses nothing and gets nothing twice.
    """

    def __init__(self, owner: str):
        self.owner = owner         # actor_of(user): only its own user reads a run
        self.events: list = []
        self.ended_at: float | None = None
        self._readers: set[asyncio.Queue] = set()

    @property
    def readers(self) -> int:
        """Requests reading the run now."""
        return len(self._readers)

    def add(self, event) -> None:
        self.events.append(event)
        for reader in self._readers:
            reader.put_nowait(event)

    def end(self) -> None:
        self.ended_at = time.monotonic()
        for reader in self._readers:
            reader.put_nowait(_END)

    async def follow(self) -> AsyncIterator:
        """Every event from the first, then each new one, until the run ends."""
        reader: asyncio.Queue = asyncio.Queue()
        backlog, ended = list(self.events), self.ended_at is not None
        self._readers.add(reader)
        try:
            for event in backlog:
                yield event
            if ended:
                return
            while (event := await reader.get()) is not _END:
                yield event
        finally:
            self._readers.discard(reader)


class Runs:
    """This microVM's runs, by runId."""

    def __init__(self):
        self._logs: dict[str, RunLog] = {}

    def get(self, run_id: str, owner: str) -> RunLog | None:
        self._forget_ended()
        log = self._logs.get(run_id)
        return log if log is not None and log.owner == owner else None

    def start(self, run_id: str, owner: str, events: Callable[[], Iterator]) -> RunLog:
        """
        Runs `events` (a synchronous generator: the agent, with boto3 and
        httpx) start to finish in a worker thread, into a new log. The event
        loop stays free, so /ping answers while the agent works (a blocked
        ping gets the session recycled).
        """
        loop = asyncio.get_running_loop()
        log = self._logs[run_id] = RunLog(owner)

        def run() -> None:
            try:
                for event in events():
                    loop.call_soon_threadsafe(log.add, event)
            except Exception:
                logger.exception("A run failed outside the agent", extra={"run_id": run_id})
            finally:
                loop.call_soon_threadsafe(log.end)

        loop.run_in_executor(None, contextvars.copy_context().run, run)
        return log

    def busy(self) -> bool:
        """Whether a run is going: the session isn't idle, even with nobody reading."""
        return any(log.ended_at is None for log in list(self._logs.values()))

    def _forget_ended(self) -> None:
        cutoff = time.monotonic() - KEEP_ENDED_SECONDS
        for run_id, log in list(self._logs.items()):
            if log.ended_at is not None and log.ended_at < cutoff:
                del self._logs[run_id]
