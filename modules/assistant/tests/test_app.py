"""The AgentCore Runtime contract for AG-UI: POST /invocations streams AG-UI events as SSE, GET /ping answers."""
import json

from starlette.testclient import TestClient

from assistant.app import create_app

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def sse_events(response) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


def client(ctx) -> TestClient:
    return TestClient(create_app(lambda: ctx.usage, lambda: ctx.state.agents, lambda: ctx.history))


def run_input(ctx, text: str) -> dict:
    """What an AG-UI client (@ag-ui/client's HttpAgent) sends."""
    return {"threadId": ctx.session, "runId": "run-1", "state": {}, "tools": [], "context": [],
            "forwardedProps": {}, "messages": [{"id": "m1", "role": "user", "content": text}]}


def test_invocations_stream_agui_events(ctx, alice):
    ctx.script(ctx.answer("Hello!"))

    response = client(ctx).post("/invocations", json=run_input(ctx, "Hi"),
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    ctx.check_order(events)
    assert ctx.text(events) == "Hello!"   # streamed in pieces, each exactly once
    assert events[0] == {"type": "RUN_STARTED", "threadId": ctx.session, "runId": "run-1"}
    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["runId"] == "run-1"


def test_errors_are_run_error_events_with_status_200(ctx, alice):
    # The stream has started: the code the frontend needs (PLAN_LIMIT_REACHED:
    # show "Upgrade") travels in the event.
    ctx.set_usage("t1", messages=50, tokens=0)
    response = client(ctx).post("/invocations", json=run_input(ctx, "Hi"),
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})
    assert response.status_code == 200
    assert [e["type"] for e in sse_events(response)] == ["RUN_STARTED", "RUN_ERROR"]
    assert sse_events(response)[-1]["code"] == "PLAN_LIMIT_REACHED"


def test_a_request_that_is_not_agui_is_refused_before_anything_runs(ctx, alice):
    response = client(ctx).post("/invocations", json={"prompt": "Hi"},
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})
    assert response.status_code == 400 and "RunAgentInput" in response.json()["error"]


def test_ping(ctx):
    response = client(ctx).get("/ping")
    assert response.status_code == 200 and response.json()["status"] == "Healthy"
