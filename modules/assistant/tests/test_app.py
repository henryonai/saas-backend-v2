"""The AgentCore Runtime contract, over HTTP: POST /invocations streams SSE, GET /ping answers."""
import json

from starlette.testclient import TestClient

from assistant.app import create_app

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def sse_events(response) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


def client(ctx) -> TestClient:
    return TestClient(create_app(lambda: ctx.usage, lambda: ctx.state.agents))


def test_invocations_stream_server_sent_events(ctx, alice):
    ctx.script(ctx.answer("Hello!"))

    response = client(ctx).post("/invocations", json={"prompt": "Hi"},
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    assert ctx.text(events) == "Hello!"   # streamed in pieces, each exactly once
    assert events[-1]["type"] == "done" and events[-1]["session_id"] == ctx.session


def test_errors_are_events_with_status_200(ctx, alice):
    # AgentCore would turn a 4xx from the agent into a generic 424, so the
    # status the frontend needs (402: show "Upgrade") travels in the event.
    ctx.set_usage("t1", messages=50, tokens=0)
    response = client(ctx).post("/invocations", json={"prompt": "Hi"},
                                headers={"Authorization": ctx.id_token(alice), SESSION_HEADER: ctx.session})
    assert response.status_code == 200
    assert sse_events(response)[0]["status"] == 402


def test_ping(ctx):
    response = client(ctx).get("/ping")
    assert response.status_code == 200 and response.json()["status"] == "Healthy"
