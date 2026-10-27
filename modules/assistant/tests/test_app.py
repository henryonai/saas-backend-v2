"""The AgentCore Runtime contract for AG-UI: POST /invocations streams AG-UI events as SSE, GET /ping answers."""
import json
import time
from uuid import uuid4

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def sse_events(response) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


def run_input(ctx, run_id: str) -> dict:
    """
    What an AG-UI client (@ag-ui/client's HttpAgent) sends: here, the run the
    HTTP API gave the message, in the workspace the page is in.
    """
    return {"threadId": ctx.session, "runId": run_id, "state": {}, "tools": [], "context": [],
            "forwardedProps": {"tenantId": "t1"}, "messages": [{"id": "m1", "role": "user", "content": "Hi"}]}


def test_invocations_stream_agui_events(ctx, alice):
    ctx.script(ctx.answer("Hello!"))
    run_id = ctx.post("Hi", alice)

    response = ctx.runtime.post("/invocations", json=run_input(ctx, run_id),
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    ctx.check_order(events)
    assert ctx.text(events) == "Hello!"   # streamed in pieces, each exactly once
    started = events[0]
    assert {k: started[k] for k in ("type", "threadId", "runId")} == {"type": "RUN_STARTED", "threadId": ctx.session,
                                                                     "runId": run_id}
    assert abs(started["timestamp"] - time.time() * 1000) < 60_000   # when the run started: which log of it
    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["runId"] == run_id


def test_errors_are_run_error_events_with_status_200(ctx, alice):
    # The stream has started: the code the frontend needs travels in the event.
    response = ctx.runtime.post("/invocations", json=run_input(ctx, str(uuid4())),   # a run never sent
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})
    assert response.status_code == 200
    assert [e["type"] for e in sse_events(response)] == ["RUN_STARTED", "RUN_ERROR"]
    assert sse_events(response)[-1]["code"] == "NOT_FOUND"


def test_a_request_that_is_not_agui_is_refused_before_anything_runs(ctx, alice):
    response = ctx.runtime.post("/invocations", json={"prompt": "Hi"},
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})
    assert response.status_code == 400 and "RunAgentInput" in response.json()["error"]


def test_ping(ctx):
    response = ctx.runtime.get("/ping")
    assert response.status_code == 200 and response.json()["status"] == "Healthy"
