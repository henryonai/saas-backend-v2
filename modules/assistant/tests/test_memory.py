"""
AgentCoreMemory against a stubbed boto3 client: botocore checks every request
against the real AgentCore API model, so a wrong parameter fails here.
"""
import boto3
import pytest
from botocore.stub import ANY, Stubber

from assistant.memory import AgentCoreMemory

MEMORY_ID = "saas_dev_assistant-abcdefghij"


@pytest.fixture
def stubbed():
    client = boto3.client("bedrock-agentcore", region_name="ap-southeast-1")
    with Stubber(client) as stub:
        yield AgentCoreMemory(client, MEMORY_ID), stub
        stub.assert_no_pending_responses()


def test_recall_searches_only_the_actors_namespaces(stubbed):
    memory, stub = stubbed
    stub.add_response("retrieve_memory_records", {"memoryRecordSummaries": [
        {"memoryRecordId": "mem-" + "0" * 40, "memoryStrategyId": "facts-abcdefghij",
         "namespaces": ["/actors/t1:alice/facts/"], "content": {"text": "Works in purchasing"},
         "createdAt": "2026-10-01T00:00:00Z"},
    ]}, {"memoryId": MEMORY_ID, "namespacePath": "/actors/t1:alice/",
         "searchCriteria": {"searchQuery": "orders?", "topK": 5}, "maxResults": 5})

    assert memory.recall("t1:alice", "orders?") == ["Works in purchasing"]


def test_remember_saves_a_conversational_event(stubbed):
    memory, stub = stubbed
    stub.add_response("create_event", {"event": {
        "memoryId": MEMORY_ID, "actorId": "t1:alice", "sessionId": "s1", "eventId": "e1",
        "eventTimestamp": "2026-10-01T00:00:00Z", "payload": []}}, {
        "memoryId": MEMORY_ID, "actorId": "t1:alice", "sessionId": "s1", "eventTimestamp": ANY,
        "payload": [{"conversational": {"role": "USER", "content": {"text": "Hi"}}},
                    {"conversational": {"role": "ASSISTANT", "content": {"text": "Hello!"}}}]})

    memory.remember("t1:alice", "s1", "Hi", "Hello!")


def test_memory_failures_do_not_fail_the_message(stubbed):
    memory, stub = stubbed
    stub.add_client_error("retrieve_memory_records", "ThrottledException", http_status_code=429)
    stub.add_client_error("create_event", "ServiceException", http_status_code=500)

    assert memory.recall("t1:alice", "orders?") == []
    memory.remember("t1:alice", "s1", "Hi", "Hello!")  # logged, not raised
