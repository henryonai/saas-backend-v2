import json
import time
from typing import Annotated

import boto3
import pytest
from aws_lambda_powertools.event_handler import Depends
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from pydantic import ValidationError

from shared import http, outbox
from shared.auth import CurrentUser, Workspace, get_current_user, user_from_claims, workspaces_in
from shared.dynamo import ConditionFailed, transact
from shared.errors import Conflict, Forbidden, InvalidRequest, ServiceBusy, Unauthorized
from shared.events import Event, EventBridgePublisher
from shared.idempotency import IDEMPOTENCY, fingerprint, idempotency_put, replay
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


def test_the_request_picks_one_of_the_workspaces_its_token_lists():
    token = claims("u1", "t1", "owner", plan="pro", workspaces={"t2": ("member", "free")})
    assert workspaces_in(token) == {"t1": Workspace("owner", "pro"), "t2": Workspace("member", "free")}
    in_t1, in_t2 = user_from_claims(token, "t1"), user_from_claims(token, "t2")
    assert (in_t1.tenant_id, in_t1.role, in_t1.plan) == ("t1", "owner", "pro")
    assert (in_t2.tenant_id, in_t2.role, in_t2.plan) == ("t2", "member", "free")
    nowhere = user_from_claims(token)                       # no workspace chosen: none, never a default
    assert (nowhere.tenant_id, nowhere.role, nowhere.plan) == (None, None, None)
    with pytest.raises(Forbidden, match="Choose one of your workspaces"):
        nowhere.require_tenant()


def test_a_workspace_the_token_does_not_list_is_refused():
    token = claims("u1", "t1", "owner")
    with pytest.raises(Unauthorized, match="refresh"):      # 401: the client refreshes the token once and retries
        user_from_claims(token, "t2")
    with pytest.raises(Unauthorized, match="refresh"):
        user_from_claims(claims("newbie"), "t1")


@pytest.mark.parametrize("listed", ["not json", '["t1"]', '{"t1": {"role": "owner"}}',
                                    '{"t1": {"role": "superuser", "plan": "pro"}}'])
def test_workspaces_claims_we_did_not_write_are_refused(listed):
    with pytest.raises(Unauthorized):
        user_from_claims({**claims("u1"), "workspaces": listed})


def test_routes_act_in_the_workspace_named_by_the_x_tenant_id_header():
    router = Router()

    @router.get("/whoami")
    def whoami(user: Annotated[CurrentUser, Depends(get_current_user)]):
        return {"tenant_id": user.require_tenant(), "role": user.role}

    handler = http.create_handler(http.create_app(router, title="Test"))
    two = claims("u1", "t1", "owner", workspaces={"t2": ("member", "free")})
    assert call(handler, "GET", "/whoami", two, tenant_id="t2") == (200, {"tenant_id": "t2", "role": "member"})
    assert call(handler, "GET", "/whoami", two, tenant_id="t3")[0] == 401
    assert call(handler, "GET", "/whoami", two)[0] == 403    # several workspaces and no header: choose one


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


def test_each_request_logs_its_route_status_tenant_and_user_and_an_unhandled_error_its_traceback():
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
    error, request = logs()                 # the error's traceback, then the request that worked
    assert error["level"] == "ERROR" and error["message"] == "Unhandled error"
    assert "table not found" in error["exception"]
    assert (error["correlation_id"], error["tenant_id"], error["user_id"]) == ("test", "t1", "alice")
    assert request["level"] == "INFO" and request["message"] == "Request"
    assert (request["route"], request["status"], request["tenant_id"], request["user_id"]) == \
        ("GET /fine", 200, "t1", "alice")


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


def test_a_request_has_the_same_fingerprint_whatever_the_order_of_its_fields():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_an_idempotency_key_is_saved_once_and_replays_what_it_created(monkeypatch):
    table = create_table("idempotency-test")
    transact(table, **{IDEMPOTENCY: idempotency_put("TENANT#t1", "alice", "k", "req", "o-1")})
    with pytest.raises(ConditionFailed) as e:   # the retry: nothing is written
        transact(table, **{IDEMPOTENCY: idempotency_put("TENANT#t1", "alice", "k", "req", "o-2")})
    assert e.value.failed == {IDEMPOTENCY}

    assert replay(table, "TENANT#t1", "alice", "k", "req") == "o-1"
    with pytest.raises(InvalidRequest):
        replay(table, "TENANT#t1", "alice", "k", "another request")
    later = time.time() + 25 * 60 * 60
    monkeypatch.setattr("shared.idempotency.time.time", lambda: later)   # expired; TTL hasn't removed it yet
    with pytest.raises(Conflict):
        replay(table, "TENANT#t1", "alice", "k", "req")
    transact(table, **{IDEMPOTENCY: idempotency_put("TENANT#t1", "alice", "k", "req", "o-3")})   # free again


@pytest.mark.parametrize("groups", ["[platform-admins]", "[support platform-admins]", ["platform-admins"]])
def test_platform_admins_are_the_user_pool_group(groups):
    assert user_from_claims({**claims("ops"), "cognito:groups": groups}).platform_admin
    assert not user_from_claims({**claims("ops"), "cognito:groups": "[support]"}).platform_admin
    assert not user_from_claims(claims("alice", "t1", "owner")).platform_admin
    with pytest.raises(Forbidden):
        user_from_claims(claims("alice", "t1", "owner")).require_platform_admin()


def test_a_modules_copy_of_a_workspaces_status_ignores_older_events():
    from shared.workspace_status import WorkspaceInactive, active_condition, check_active, save
    table = create_table("status-test")
    check_active(table, "t1")                                          # no copy: active
    save(table, "TenantSuspended", {"tenant_id": "t1", "occurred_at": "2026-10-28T10:00:00+00:00"})
    save(table, "TenantRestored", {"tenant_id": "t1", "occurred_at": "2026-10-28T09:00:00+00:00"})   # older: late
    with pytest.raises(WorkspaceInactive, match="suspended") as e:
        check_active(table, "t1")
    assert e.value.status_code == 403
    with pytest.raises(ConditionFailed) as failed:                     # in a transaction, as orders does
        transact(table, active=active_condition(table, "t1"),
                 item={"Put": {"Item": {"pk": "TENANT#t1", "sk": "X"}}})
    assert failed.value.failed == {"active"}

    save(table, "TenantUnsuspended", {"tenant_id": "t1", "occurred_at": "2026-10-28T11:00:00+00:00"})
    check_active(table, "t1")
    transact(table, active=active_condition(table, "t1"), item={"Put": {"Item": {"pk": "TENANT#t1", "sk": "X"}}})
    save(table, "TenantDeletionScheduled", {"tenant_id": "t1", "occurred_at": "2026-10-28T12:00:00+00:00"})
    with pytest.raises(WorkspaceInactive, match="being deleted"):
        check_active(table, "t1")
    check_active(table, "t2")                                          # other workspaces are untouched
