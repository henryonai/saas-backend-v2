"""
Checks template.yaml for the event plumbing rules, so a module added later
(or a project made from this template) can't skip them:

- every EventBridge rule target retries and has a dead-letter queue the rule may send to
- every queue a function reads has a dead-letter queue, a long enough visibility
  timeout, a concurrency cap, partial batch responses and an age alarm
- every dead-letter queue has an alarm
- every stream a function reads has a failure destination and a lag alarm
- every rule has an alarm for events it couldn't even put in its dead-letter queue
"""
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "template.yaml"


class Loader(yaml.SafeLoader):
    """Reads CloudFormation's short tags (!Ref, !GetAtt, ...) as plain data."""


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if suffix == "Ref":
        return {"Ref": value}
    if suffix == "GetAtt":
        return {"Fn::GetAtt": value.split(".", 1) if isinstance(value, str) else value}
    return {f"Fn::{suffix}": value}


Loader.add_multi_constructor("!", _intrinsic)

TPL = yaml.load(TEMPLATE.read_text(), Loader=Loader)  # noqa: S506 (SafeLoader subclass)
RESOURCES = TPL["Resources"]
DEFAULT_TIMEOUT = TPL["Globals"]["Function"]["Timeout"]


def of_type(kind: str) -> dict:
    return {name: r for name, r in RESOURCES.items() if r["Type"] == kind}


def target(ref) -> str:
    """The logical id a !Ref or !GetAtt points to."""
    if "Ref" in ref:
        return ref["Ref"]
    return ref["Fn::GetAtt"][0]


def metrics(metric: str) -> list[dict]:
    """Every alarm's metrics named `metric`: a plain alarm's, or one of a metric-math alarm's."""
    found = []
    for alarm in of_type("AWS::CloudWatch::Alarm").values():
        props = alarm["Properties"]
        if props.get("MetricName") == metric:
            found.append(props)
        for item in props.get("Metrics", []):
            if isinstance(item, dict) and "Fn::If" in item:   # a metric only some stacks have
                item = item["Fn::If"][1]
            stat = item.get("MetricStat", {}).get("Metric", {})
            if stat.get("MetricName") == metric:
                found.append(stat)
    return found


def referenced(value) -> set[str]:
    """Every logical id a value refers to, however deeply nested (!Select, !Split, ...)."""
    if isinstance(value, dict):
        if "Ref" in value or "Fn::GetAtt" in value:
            return {target(value)}
        return set().union(*(referenced(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(referenced(v) for v in value))
    return set()


def alarmed(metric: str, dimension: str, logical_id: str) -> bool:
    return any(logical_id in referenced(d["Value"])
               for m in metrics(metric) for d in m["Dimensions"] if d["Name"] == dimension)


def function_events(kind: str) -> list[tuple[str, dict, dict]]:
    return [(name, fn, event["Properties"])
            for name, fn in of_type("AWS::Serverless::Function").items()
            for event in (fn["Properties"].get("Events") or {}).values() if event["Type"] == kind]


RULE_TARGETS = [(name, t) for name, rule in of_type("AWS::Events::Rule").items() for t in rule["Properties"]["Targets"]]
QUEUE_READERS = function_events("SQS")
STREAM_READERS = function_events("DynamoDB")


def test_there_is_something_to_check():
    assert RULE_TARGETS and QUEUE_READERS and STREAM_READERS


@pytest.mark.parametrize(("rule", "rule_target"), RULE_TARGETS, ids=[r for r, _ in RULE_TARGETS])
def test_rule_targets_retry_and_keep_what_they_cant_deliver(rule, rule_target):
    assert rule_target.get("RetryPolicy"), "set a RetryPolicy"
    dlq = target(rule_target["DeadLetterConfig"]["Arn"])
    policies = [p["Properties"] for p in of_type("AWS::SQS::QueuePolicy").values()
                if dlq in [target(q) for q in p["Properties"]["Queues"]]]
    allowed = referenced([s["Condition"]["ArnEquals"]["aws:SourceArn"] for p in policies
                          for s in p["PolicyDocument"]["Statement"]
                          if s["Principal"] == {"Service": "events.amazonaws.com"}])
    assert rule in allowed, f"{dlq} needs a queue policy that lets {rule} send to it"
    assert alarmed("InvocationsFailedToBeSentToDlq", "RuleName", rule), f"alarm on {rule}'s lost events"


@pytest.mark.parametrize(("function", "fn", "event"), QUEUE_READERS, ids=[f for f, _, _ in QUEUE_READERS])
def test_queue_readers(function, fn, event):
    queue = target(event["Queue"])
    props = RESOURCES[queue]["Properties"]
    assert props.get("RedrivePolicy", {}).get("deadLetterTargetArn"), f"{queue} needs a dead-letter queue"
    timeout = fn["Properties"].get("Timeout", DEFAULT_TIMEOUT)
    assert props.get("VisibilityTimeout", 30) >= 6 * timeout, f"{queue}: visibility timeout under 6x {function}'s"
    assert event.get("FunctionResponseTypes") == ["ReportBatchItemFailures"], "retry only the failed messages"
    assert event.get("ScalingConfig", {}).get("MaximumConcurrency"), "cap how many run at once"
    assert alarmed("ApproximateAgeOfOldestMessage", "QueueName", queue), f"alarm on {queue}'s oldest message"


STREAM_IDS = [f"{f}-{target(e['Stream'])}" for f, _, e in STREAM_READERS]


@pytest.mark.parametrize(("function", "fn", "event"), STREAM_READERS, ids=STREAM_IDS)
def test_stream_readers(function, fn, event):
    assert event["DestinationConfig"]["OnFailure"]["Destination"], "keep what can't be processed"
    assert event.get("FunctionResponseTypes") == ["ReportBatchItemFailures"]
    assert alarmed("IteratorAge", "FunctionName", function), f"alarm on {function}'s lag"


def test_every_dead_letter_queue_has_an_alarm():
    dlqs = {target(q["Properties"]["RedrivePolicy"]["deadLetterTargetArn"])
            for q in of_type("AWS::SQS::Queue").values() if q["Properties"].get("RedrivePolicy")}
    dlqs |= {target(t["DeadLetterConfig"]["Arn"]) for _, t in RULE_TARGETS}
    dlqs |= {target(e["DestinationConfig"]["OnFailure"]["Destination"]) for _, _, e in STREAM_READERS}
    missing = sorted(q for q in dlqs if not alarmed("ApproximateNumberOfMessagesVisible", "QueueName", q))
    assert not missing, f"no alarm on {missing}"
