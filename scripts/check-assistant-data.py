"""
Checks the assistant's table against its data model
(modules/assistant/src/assistant/repository.py) and prints what doesn't fit:
each conversation's messages against its META record and the path through its
versions (versions.py), the list index, a run left behind, tool results
without their call, ratings; the users' memories and files; and the usage
records, monthly and hourly.

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

from assistant.versions import Tree

ROOT = Path(__file__).resolve().parent.parent
RUN_LIMIT = timedelta(minutes=60)   # service.RUN_LIMIT: an older run no longer blocks the conversation
CONVERSATION = re.compile(r"^TENANT#(?P<tenant>[^#]+)#USER#(?P<user>[^#]+)#CONV#(?P<thread>.+)$")
MESSAGE = re.compile(r"^(?:BRANCH#(?P<branch>\d+)#)?MSG#(?P<position>\d+)$")   # branch 0 has no prefix
HOURLY = re.compile(r"^USER#[^#]+#(?:HOUR|UPLOADS)#\d{4}-\d{2}-\d{2}T\d{2}$")   # a user's messages, uploads that hour
MEMORY = re.compile(r"^TENANT#[^#]+#USER#[^#]+#MEMORY$")
FILES = re.compile(r"^TENANT#[^#]+#USER#[^#]+#FILES$")


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


def check_conversation(pk: str, meta: dict | None, messages: dict[tuple[int, int], dict],
                       now: float) -> tuple[list[str], list[str]]:
    """
    What doesn't fit in one conversation (problems), and what is expected but
    worth knowing (notes). `messages` maps each message's (branch, position)
    to its item; the path META shows through them is what the user sees.
    """
    if meta is None:
        return [f"{len(messages)} messages without a META record (a delete that stopped half-way?)"], []
    problems, notes = [], []
    tenant, user = CONVERSATION.match(pk).group("tenant", "user")
    count = int(meta["message_count"])
    tree = Tree.load(meta.get("path"), meta.get("branches"), count)
    if strays := [node for node in messages if node[0] not in tree.branches]:
        problems.append(f"{len(strays)} messages in a branch the conversation doesn't have")
    # Past a branch's end: what an edit's delete left when it stopped half way,
    # before versions (October 2026). Not shown; later messages write over it.
    beyond = [node for node in messages if node[0] in tree.branches and node[1] > tree.branches[node[0]].end]
    if beyond:
        notes.append(f"{len(beyond)} messages past their branch's end, left by an edit before versions (not shown)")
    if len(tree.branches) > 1:
        notes.append(f"{len(tree.branches) - 1} versions (edits, answers asked for again); the path shows "
                     f"{len(tree.path)} segments")
    shown = [tree.node(p) for p in range(1, count + 1)]
    numbers = [p for (p, node) in enumerate(shown, start=1) if node in messages]
    # Messages have no TTL of their own: the conversation expires whole (its
    # META). Those saved before that change (October 2026) kept one, so the
    # oldest of an old conversation may be gone; the rest number up to
    # message_count without a gap.
    if numbers and numbers[-1] != count:
        problems.append(f"message_count is {count} but the newest message shown is at {numbers[-1]}")
    if numbers and numbers != list(range(numbers[0], numbers[-1] + 1)):
        problems.append(f"gap in the messages shown: {numbers[0]}..{numbers[-1]} has {len(numbers)}")
    if numbers and numbers[0] != 1 and all("expires_at" in messages[shown[n - 1]] for n in numbers[:1]):
        notes.append(f"messages 1..{numbers[0] - 1} expired on their own (saved before conversations expired whole)")
    elif numbers and numbers[0] != 1:
        problems.append(f"messages 1..{numbers[0] - 1} are missing")
    if tree.empty_tail and not meta.get("run"):
        problems.append("it ends in an empty version: an answer asked for again whose run never ended")
    heads = {agent: int(rev) for agent, rev in (meta.get("heads") or {}).items()}
    if heads and (meta.get("rev") is None or max(heads.values()) > int(meta["rev"])):
        problems.append("an agent's checkpoint is of a rev the conversation never had (heads)")
    if meta.get("gsi1pk") != f"TENANT#{tenant}#USER#{user}#CONVS" or meta.get("gsi1sk") != meta["updated_at"]:
        problems.append("not in the user's list as it should be (gsi1pk/gsi1sk)")
    if meta.get("title_source") not in ("message", "model", "user"):
        problems.append(f"unknown title_source {meta.get('title_source')!r}")
    # A conversation without expires_at is kept until its user deletes it (AssistantConversationDays 0).
    late = [node for node in messages if "expires_at" in meta
            and "expires_at" in messages[node] and int(messages[node]["expires_at"]) > int(meta["expires_at"])]
    if late:
        problems.append(f"{len(late)} messages expire after their conversation")
    rated = [item for item in messages.values() if "rating" in item]
    if [m for m in rated if m["rating"] not in ("up", "down") or m["message"].get("role") != "assistant"]:
        problems.append("a rating that isn't up or down on an assistant message")
    ordered = [messages[shown[n - 1]]["message"] for n in numbers]
    users = [m for m in ordered if m.get("role") == "user"]
    if users and "question_id" not in meta:
        notes.append("no question_id: last written before it existed (24 Oct 2026); its next message sets it")
    elif users and meta["question_id"] != users[-1].get("id"):
        problems.append("question_id isn't the newest user message shown (a retry would be saved twice)")
    ids = [m.get("id") for m in users]
    if len(ids) != len(set(ids)):
        problems.append("a user message is shown twice")
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
    if "expires_at" in meta and int(meta["expires_at"]) < now:
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

    metas, messages, usage, hourly, unknown = {}, defaultdict(dict), [], [], []
    memories, settings, files, names = defaultdict(list), {}, defaultdict(dict), defaultdict(dict)
    for item in items:
        if item["sk"] == "META" and CONVERSATION.match(item["pk"]):
            metas[item["pk"]] = item
        elif (message := MESSAGE.match(item["sk"])) and CONVERSATION.match(item["pk"]):
            messages[item["pk"]][int(message.group("branch") or 0), int(message.group("position"))] = item
        elif MEMORY.match(item["pk"]) and item["sk"].startswith("MEM#"):
            memories[item["pk"]].append(item)
        elif MEMORY.match(item["pk"]) and item["sk"] == "SETTINGS":
            settings[item["pk"]] = item
        elif FILES.match(item["pk"]) and item["sk"].startswith("FILE#"):
            files[item["pk"]][item["sk"].removeprefix("FILE#")] = item
        elif FILES.match(item["pk"]) and item["sk"].startswith("NAMED#"):
            names[item["pk"]][item["sk"]] = item
        elif item["sk"].startswith("USAGE#"):
            usage.append(item)
        elif HOURLY.match(item["sk"]) and item["pk"].startswith("TENANT#"):
            hourly.append(item)
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
    for item in hourly:
        if "expires_at" not in item:
            problems.append(f"hourly count {item['pk']} {item['sk']} never expires")
        elif int(item["expires_at"]) < now:
            notes.append(f"hourly count {item['pk']} {item['sk']}: expired, waiting for TTL to remove it")
    for pk, kept in memories.items():
        if len(kept) > 100:   # service.MAX_MEMORIES, checked before each one is saved
            notes.append(f"{pk}: {len(kept)} memories, past the 100 kept (two saved at once?)")
        problems += [f"{pk} {m['sk']}: a memory without text" for m in kept if not str(m.get("text", "")).strip()]
    for pk, by_id in files.items():
        for file_id, item in by_id.items():
            versions = [int(v["version"]) for v in item.get("versions", [])]
            if versions != list(range(1, len(versions) + 1)):
                problems.append(f"{pk} FILE#{file_id}: versions aren't 1..{len(versions)}")
            if item.get("status") == "pending" and "expires_at" not in item:
                problems.append(f"{pk} FILE#{file_id}: an upload never sent that never expires")
            if item.get("status") == "ready" and "expires_at" in item:
                problems.append(f"{pk} FILE#{file_id}: a file kept that expires")
            if item.get("status") == "pending" and int(item.get("expires_at", now)) < now:
                notes.append(f"{pk} FILE#{file_id}: an upload never sent, waiting for TTL to remove it")
        for sk, item in names[pk].items():
            if item.get("file_id") not in by_id:
                notes.append(f"{pk} {sk}: names a file that was deleted (saved again, it starts a new one)")
    problems += [f"a record outside the data model: {key}" for key in unknown]

    running = sum(1 for meta in metas.values() if meta.get("run"))
    rated = sum(1 for m in messages.values() for item in m.values() if "rating" in item)
    print(f"{name}: {len(metas)} conversations ({running} answering now), "
          f"{sum(len(m) for m in messages.values())} messages ({rated} answers rated), "
          f"{sum(len(m) for m in memories.values())} memories, {sum(len(f) for f in files.values())} files, "
          f"{len(usage)} usage records, {len(hourly)} hourly counts")
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
