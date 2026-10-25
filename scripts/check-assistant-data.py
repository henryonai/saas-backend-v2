"""
Checks the assistant's table against its data model
(modules/assistant/src/assistant/repository.py) and prints what doesn't fit:
each conversation's messages against its META record, the list index, a run
left behind, tool results without their call, and the usage records.

    uv run python scripts/check-assistant-data.py --config-env dev
    uv run python scripts/check-assistant-data.py --config-env dev --tenant <tenant_id>

Read-only: one Scan of the table, which is fine for dev; in prod, pass
--tenant. Exits 1 if anything doesn't fit.
"""
import argparse
import re
import sys
import time
import tomllib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

ROOT = Path(__file__).resolve().parent.parent
RUN_LIMIT = timedelta(minutes=60)   # service.RUN_LIMIT: an older run no longer blocks the conversation
CONVERSATION = re.compile(r"^TENANT#(?P<tenant>[^#]+)#USER#(?P<user>[^#]+)#CONV#(?P<thread>.+)$")


def stack_settings(config_env: str) -> tuple[str, str]:
    config = tomllib.loads((ROOT / "samconfig.toml").read_text())
    return config[config_env]["deploy"]["parameters"]["stack_name"], config["default"]["global"]["parameters"]["region"]


def scan(table, tenant: str | None) -> list[dict]:
    kwargs = {"FilterExpression": Attr("pk").begins_with(f"TENANT#{tenant}")} if tenant else {}
    items, page = [], table.scan(**kwargs)
    items += page["Items"]
    while "LastEvaluatedKey" in page:
        page = table.scan(ExclusiveStartKey=page["LastEvaluatedKey"], **kwargs)
        items += page["Items"]
    return items


def check_conversation(pk: str, meta: dict | None, messages: dict[int, dict],
                       now: float) -> tuple[list[str], list[str]]:
    """
    What doesn't fit in one conversation (problems), and what is expected but
    worth knowing (notes). `messages` maps each MSG#n to its item.
    """
    if meta is None:
        return [f"{len(messages)} messages without a META record (a delete that stopped half-way?)"], []
    problems, notes = [], []
    tenant, user = CONVERSATION.match(pk).group("tenant", "user")
    count = int(meta["message_count"])
    numbers = sorted(messages)
    # TTL removes each message some days after it was written, oldest first: the
    # ones left are the newest, numbered up to message_count without a gap.
    if numbers and numbers[-1] != count:
        problems.append(f"message_count is {count} but the newest message is MSG#{numbers[-1]:06d}")
    if numbers and numbers != list(range(numbers[0], numbers[-1] + 1)):
        problems.append(f"gap in the messages: {numbers[0]}..{numbers[-1]} has {len(numbers)}")
    if meta.get("gsi1pk") != f"TENANT#{tenant}#USER#{user}#CONVS" or meta.get("gsi1sk") != meta["updated_at"]:
        problems.append("not in the user's list as it should be (gsi1pk/gsi1sk)")
    if meta.get("title_source") not in ("message", "model", "user"):
        problems.append(f"unknown title_source {meta.get('title_source')!r}")
    late = [n for n in numbers if int(messages[n]["expires_at"]) > int(meta["expires_at"])]
    if late:
        problems.append(f"{len(late)} messages expire after their conversation")
    ordered = [messages[n]["message"] for n in numbers]
    users = [m for m in ordered if m.get("role") == "user"]
    if users and "question_id" not in meta:
        notes.append("no question_id: last written before it existed (24 Oct 2026); its next message sets it")
    elif users and meta["question_id"] != users[-1].get("id"):
        problems.append("question_id isn't the newest user message's id (a retry would be saved twice)")
    ids = [m.get("id") for m in users]
    if len(ids) != len(set(ids)):
        problems.append("a user message is saved twice")
    calls = {call["id"] for m in ordered for call in m.get("toolCalls") or []}
    orphans = [m for m in ordered if m.get("role") == "tool" and m.get("toolCallId") not in calls]
    # A tool result's call can only be missing if TTL already removed the older call.
    if orphans and numbers and numbers[0] == 1:
        problems.append(f"{len(orphans)} tool results without the call they answer")
    run = meta.get("run")
    if run:
        since = datetime.fromisoformat(run["since"].replace("Z", "+00:00"))
        if datetime.now(UTC) - since > RUN_LIMIT:
            problems.append(f"a run left behind since {run['since']} (its answer was never saved)")
    if int(meta["expires_at"]) < now:
        notes.append("expired, waiting for TTL to remove it (the app already hides it)")
    return problems, notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-env", default="dev", help="dev or prod (samconfig.toml)")
    parser.add_argument("--tenant", help="only this workspace (tenant_id)")
    args = parser.parse_args()

    stack, region = stack_settings(args.config_env)
    name = boto3.client("cloudformation", region_name=region).describe_stack_resource(
        StackName=stack, LogicalResourceId="AssistantTable")["StackResourceDetail"]["PhysicalResourceId"]
    items = scan(boto3.resource("dynamodb", region_name=region).Table(name), args.tenant)
    now = time.time()

    metas, messages, usage, unknown = {}, defaultdict(dict), [], []
    for item in items:
        if item["sk"] == "META" and CONVERSATION.match(item["pk"]):
            metas[item["pk"]] = item
        elif item["sk"].startswith("MSG#") and CONVERSATION.match(item["pk"]):
            messages[item["pk"]][int(item["sk"][4:])] = item
        elif item["sk"].startswith("USAGE#"):
            usage.append(item)
        else:
            unknown.append(f"{item['pk']} {item['sk']}")

    problems, notes = [], []
    for pk in sorted(set(metas) | set(messages)):
        thread = CONVERSATION.match(pk).group("thread")
        found, noted = check_conversation(pk, metas.get(pk), messages[pk], now)
        problems += [f"conversation {thread}: {p}" for p in found]
        notes += [f"conversation {thread}: {n}" for n in noted]
    for item in usage:
        if int(item.get("tokens", 0)) != int(item.get("input_tokens", 0)) + int(item.get("output_tokens", 0)):
            problems.append(f"usage {item['pk']} {item['sk']}: tokens isn't input_tokens + output_tokens")
    problems += [f"a record outside the data model: {key}" for key in unknown]

    running = sum(1 for meta in metas.values() if meta.get("run"))
    print(f"{name}: {len(metas)} conversations ({running} answering now), "
          f"{sum(len(m) for m in messages.values())} messages, {len(usage)} usage records")
    for item in sorted(usage, key=lambda i: (i["pk"], i["sk"])):
        print(f"  usage {item['pk'].removeprefix('TENANT#')} {item['sk'].removeprefix('USAGE#')}: "
              f"{item.get('messages', 0)} messages, {item.get('tokens', 0)} tokens, "
              f"{item.get('sandbox_sessions', 0)} sandbox sessions")
    for note in notes:
        print("note", note)
    for problem in problems:
        print("PROBLEM", problem)
    print("All records fit the data model." if not problems else f"{len(problems)} problems.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
