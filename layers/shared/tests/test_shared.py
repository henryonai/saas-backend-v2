import json

import boto3
import pytest
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from pydantic import ValidationError

from shared import http, outbox
from shared.auth import CurrentUser
from shared.dynamo import ConditionFailed, transact
from shared.errors import Forbidden, ServiceBusy
from shared.events import Event, EventBridgePublisher
from shared.ids import sortable_id
from shared.outbox import outbox_put, relay
from shared.plans import get_limit, get_plan
from shared.settings import BaseAppSettings
from testkit.aws import create_table
from testkit.fakes import FakePublisher
from testkit.http import FakeContext, call, claims, http_event
from testkit.logs import captured_logs
from testkit.outbox import outbox_events


class ExampleSettings(BaseAppSettings):
    some_table: str


def test_missing_setting_fails_fast(monkeypatch):
    monkeypatch.delenv("SOME_TABLE", raising=False)
    with pytest.raises(ValidationError, match="some_table"):
        ExampleSettings(_env_file=None)


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("SOME_TABLE", "orders-dev")
    assert ExampleSettings(_env_file=None).some_table == "orders-dev"


def test_role_check():
    member = CurrentUser("u1", "u1@x.com", "t1", "member", "free")
    with pytest.raises(Forbidden):
        member.require_role("owner", "admin")
    assert member.require_role("member") == "t1"


def test_unknown_plan_falls_back_to_free():
    assert get_limit("nonexistent", "max_users") == get_limit("free", "max_users")
    assert get_limit("business", "max_orders") is None
    assert get_plan(None)["name"] == "Free"


def test_sortable_ids_sort_by_time_and_can_be_derived():
    assert sortable_id(at="2026-01-01T00:00:00+00:00") < sortable_id(at="2026-01-02T00:00:00+00:00")
    assert sortable_id(at="2026-01-01T00:00:00+00:00", unique="e1") == "1767225600000-e1"


def test_publisher_raises_when_eventbridge_rejects_the_event():
    client = boto3.client("events")
    with Stubber(client) as stub:
        stub.add_response("put_events", {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "ThrottlingException", "ErrorMessage": "Rate exceeded"}],
        })
        with pytest.raises(RuntimeError, match="ThrottlingException"):
            EventBridgePublisher(client, "bus").publish("OrderCreated", {"tenant_id": "t1"})


def test_transaction_reports_which_condition_failed():
    table = create_table("shared-test")
    table.put_item(Item={"pk": "A", "sk": "A"})
    exists_already = {"Put": {"Item": {"pk": "A", "sk": "A"}, "ConditionExpression": "attribute_not_exists(pk)"}}
    with pytest.raises(ConditionFailed) as error:
        transact(table, first=exists_already, other={"Put": {"Item": {"pk": "B", "sk": "B"}}})
    assert error.value.failed == {"first"}
    assert "Item" not in table.get_item(Key={"pk": "B", "sk": "B"})   # all or nothing


def stream_event(*items: dict) -> dict:
    """A DynamoDB stream batch as the relay receives it."""
    serializer = boto3.dynamodb.types.TypeSerializer()
    return {"Records": [{
        "eventID": str(i), "eventName": "INSERT", "eventSource": "aws:dynamodb",
        "eventSourceARN": "arn:aws:dynamodb:ap-southeast-1:123456789012:table/t/stream/1",
        "dynamodb": {"SequenceNumber": str(i), "NewImage": {k: serializer.serialize(v) for k, v in item.items()}},
    } for i, item in enumerate(items, start=1)]}


def test_relay_publishes_saved_events():
    table = create_table("outbox-test")
    event = Event("OrderCreated", "t1", {"order_id": "o1"})
    transact(table, event=outbox_put(event))
    [saved] = table.scan()["Items"]

    publisher = FakePublisher()
    result = relay(stream_event(saved, {"pk": "TENANT#t1", "sk": "ORDER#o1"}), FakeContext(), publisher)
    assert result == {"batchItemFailures": []}
    assert publisher.types() == ["OrderCreated"]                  # non-outbox items are ignored
    assert publisher.last()["detail"] == {**event.detail()}
    assert outbox_events(table)[0]["detail"]["event_id"] == event.event_id


def test_relay_retries_only_the_event_that_failed():
    class FlakyPublisher(FakePublisher):
        def publish(self, event_type, detail):
            if event_type == "Broken":
                raise RuntimeError("EventBridge throttled")
            super().publish(event_type, detail)

    items = [outbox_put(Event(t, "t1"))["Put"]["Item"] for t in ("Broken", "OrderCreated")]
    publisher = FlakyPublisher()
    with captured_logs(outbox.logger) as logs:
        result = relay(stream_event(*items), FakeContext(), publisher)
    assert result == {"batchItemFailures": [{"itemIdentifier": "1"}]}
    assert publisher.types() == ["OrderCreated"]
    assert json.loads(items[1]["detail"])["tenant_id"] == "t1"
    [failure] = [line for line in logs() if line["level"] == "WARNING"]   # the why, not only the retry
    assert "EventBridge throttled" in failure["exception"]


def test_an_unhandled_error_is_logged_with_the_request_tenant_and_user():
    router = Router()

    @router.get("/broken")
    def broken():
        raise RuntimeError("table not found")

    @router.get("/fine")
    def fine():
        return {"ok": True}

    handler = http.create_handler(http.create_app(router, title="Test"))
    alice = claims("alice", "t1", "owner")
    with captured_logs(http.logger) as logs:
        with pytest.raises(RuntimeError):   # still an error to Lambda: API Gateway answers 500
            handler(http_event("GET", "/broken", alice), FakeContext())
        assert call(handler, "GET", "/fine", alice) == (200, {"ok": True})
    [line] = logs()                         # one line, and none for the request that worked
    assert line["level"] == "ERROR" and line["message"] == "Unhandled error"
    assert "table not found" in line["exception"]
    assert (line["correlation_id"], line["tenant_id"], line["user_id"]) == ("test", "t1", "alice")


class CollidingTable:
    """A table whose transactions collide with others `collisions` times (real DynamoDB does this)."""
    name = "t"

    def __init__(self, collisions: int):
        self.calls = 0
        table = self

        class Client:
            def transact_write_items(self, **kwargs):
                table.calls += 1
                if table.calls <= collisions:
                    raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                                       "CancellationReasons": [{"Code": "TransactionConflict"}]},
                                      "TransactWriteItems")

        self.meta = type("Meta", (), {"client": Client()})()


def test_colliding_transactions_are_retried():
    table = CollidingTable(collisions=2)
    transact(table, item={"Put": {"Item": {"pk": "A", "sk": "A"}}})
    assert table.calls == 3


def test_transactions_that_keep_colliding_ask_the_caller_to_retry_later():
    table = CollidingTable(collisions=100)
    with pytest.raises(ServiceBusy):
        transact(table, item={"Put": {"Item": {"pk": "A", "sk": "A"}}})
