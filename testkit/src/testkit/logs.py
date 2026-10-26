"""Reads what the Powertools loggers print: the JSON lines CloudWatch would get."""
import io
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager


@contextmanager
def captured_logs(logger) -> Iterator[Callable[[], list[dict]]]:
    """
    Sends a Powertools Logger's output to memory for the block (every Logger of the same
    service shares its handler, so their lines too). Call the result for the
    lines so far:

        with captured_logs(logger) as logs:
            ...
        assert logs()[-1]["message"] == "Unhandled error"
    """
    buffer = io.StringIO()
    handler = logger.registered_handler
    previous = handler.setStream(buffer)
    try:
        yield lambda: [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    finally:
        handler.setStream(previous)
