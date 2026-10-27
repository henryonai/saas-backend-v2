# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3", "pycognito", "requests"]
# ///
"""
Smoke test for the assistant on a deployed stack, run after sam deploy with
its code (scripts/package-assistant.py). With the real model, AgentCore and
S3, it creates, reads, updates and deletes through the HTTP API and the AG-UI
endpoint, as the web app does, and after each step reads what DynamoDB and S3
hold (repository.py's data model), printing PASS/FAIL per check:

  conversations (send, open, list, rename, rate, delete, the checkpoints'
  purge); versions (an edit, showing another, the agent following the
  version shown, an answer asked for again, and how META and the branches
  store them); Stop in the middle of an answer (what is kept, the stopped
  call's tokens counted, the next message going on from it); an answer asked
  for again that fails before writing anything (the old one shown again, the
  agent starting again from the history); a long conversation read back past
  its latest 200 messages;
  memory (add, edit, the agent saving and recalling, memory off, the limit,
  delete, forget everything, and that their text is never logged); files
  (uploads with the presigned POST and what it refuses, the pending tag and
  TTL, attachments the model reads, files the agent writes with versions,
  links, deletes, the bucket's rules); on a Pro workspace, the sandbox's
  files (open_file, save_file); and a member who leaves the workspace, whose
  conversations, memories, files and checkpoints the purge worker deletes.

    uv run scripts/smoke-assistant.py [--stack saas-dev]

Answers come from the stack's real model, so a check about what it says can
fail now and then on a model that ignores an instruction: read the detail.
Uses your AWS credentials to create three test users (one's workspace is set
to Pro in the accounts table, one joins and leaves another's), reads the
tables, the bucket and the logs, and writes to its own conversations' records
(a long one, and a run made to fail): run it against dev, not prod. Their logins
go to .aws-sam/smoke-users.json (gitignored), never printed. It costs a few
cents of model tokens and one sandbox session.
"""
import argparse
import base64
import hashlib
import json
import secrets
import struct
import sys
import time
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path

import boto3
import requests
from boto3.dynamodb.conditions import Key
from pycognito import Cognito

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--stack", default="saas-dev")
STACK = parser.parse_args().stack
session = boto3.session.Session()
region = session.region_name
cloudformation = session.client("cloudformation")
outputs = cloudformation.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
out = {o["OutputKey"]: o["OutputValue"] for o in outputs}
resources = {r["LogicalResourceId"]: r["PhysicalResourceId"]
             for page in cloudformation.get_paginator("list_stack_resources").paginate(StackName=STACK)
             for r in page["StackResourceSummaries"]}
API, POOL, CLIENT, AGENT = out["ApiUrl"], out["UserPoolId"], out["UserPoolClientId"], out.get("AssistantUrl")
if not AGENT:
    sys.exit(f"{STACK} has no assistant: deploy it with scripts/package-assistant.py first")
idp = session.client("cognito-idp")
dynamodb = session.resource("dynamodb")
s3 = session.client("s3")
logs = session.client("logs")
TABLE = dynamodb.Table(resources["AssistantTable"])
CHECKPOINTS = dynamodb.Table(resources["AssistantCheckpointTable"])
BUCKET = resources["AssistantFilesBucket"]
RUNTIME_LOGS = "/aws/bedrock-agentcore/runtimes/" + AGENT.split("/runtimes/")[1].split("/")[0] + "-DEFAULT"
APP_LOGS = resources["AppLogGroup"]
RUN = int(time.time())
STARTED = RUN * 1000
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({str(detail)[:300]})" if detail != "" else ""), flush=True)
    if not ok:
        failures.append(name)
    return ok


def show(r: requests.Response) -> str:
    return f"{r.status_code} {r.text[:200]}"


def wait_for(fn, timeout: int = 120, every: float = 5):
    """Polls fn() until it returns something truthy (the purge and the logs are asynchronous)."""
    deadline = time.time() + timeout
    while True:
        result = fn()
        if result or time.time() > deadline:
            return result
        time.sleep(every)


class Person:
    """A test user in the user pool, signed in with SRP; with a workspace of their own unless `joins` another."""

    def __init__(self, name: str, joins: bool = False):
        self.email = f"smoke-assistant-{name}+{RUN}@example.com"
        self.password = "Sm0ke!" + secrets.token_urlsafe(18)
        idp.admin_create_user(UserPoolId=POOL, Username=self.email, MessageAction="SUPPRESS",
                              UserAttributes=[{"Name": "email", "Value": self.email},
                                              {"Name": "email_verified", "Value": "true"}])
        idp.admin_set_user_password(UserPoolId=POOL, Username=self.email, Password=self.password, Permanent=True)
        self.workspace: str | None = None   # the one they work in: X-Tenant-Id, forwardedProps.tenantId
        self.sign_in()
        if not joins:
            r = self("POST", "/tenants", json={"name": f"Smoke assistant {name}"},
                     headers={"Idempotency-Key": str(uuid.uuid4())})   # required: a retry can't make a second
            assert r.status_code == 201, show(r)
            self.sign_in()   # the token now lists the workspace
            self.workspace = r.json()["tenant_id"]

    def sign_in(self) -> "Person":
        """New tokens, listing every workspace the user is in (with their role and its plan)."""
        self.cognito = Cognito(POOL, CLIENT, user_pool_region=region, username=self.email)
        self.cognito.authenticate(password=self.password)
        return self

    @property
    def workspaces(self) -> dict:
        """What the token lists: {tenant_id: {"role": ..., "plan": ...}}."""
        return json.loads(self.claims.get("workspaces") or "{}")

    @property
    def token(self) -> str:
        return self.cognito.id_token

    @property
    def claims(self) -> dict:
        payload = self.token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    @property
    def keys(self) -> str:
        """TENANT#<t>#USER#<u>: how the assistant table keys this user's records."""
        return f"TENANT#{self.workspace}#USER#{self.claims['sub']}"

    @property
    def prefix(self) -> str:
        """Where this user's files are in the bucket."""
        return f"{self.workspace}/{self.claims['sub']}/"

    def __call__(self, method: str, path: str, **kw) -> requests.Response:
        """As this person, in the workspace they work in (X-Tenant-Id, as a browser tab sends it)."""
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {self.token}"}
        if self.workspace:
            headers["X-Tenant-Id"] = self.workspace
        return requests.request(method, API + path, headers=headers, timeout=30, **kw)


# ---------- what is stored ----------

def items(pk: str) -> dict[str, dict]:
    """Every record under a partition key, by sort key (consistent reads: what the API just wrote)."""
    found, params = {}, {"KeyConditionExpression": Key("pk").eq(pk), "ConsistentRead": True}
    while True:
        page = TABLE.query(**params)
        found |= {item["sk"]: item for item in page["Items"]}
        if "LastEvaluatedKey" not in page:
            return found
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def conversation(person: Person, thread: str) -> dict[str, dict]:
    return items(f"{person.keys}#CONV#{thread}")


def meta(person: Person, thread: str) -> dict:
    return conversation(person, thread).get("META", {})


def checkpoints_of(person: Person, thread: str, agent: str = "assistant") -> int:
    """How many checkpoint records the agent's thread has (context.checkpoint_session: user, session, agent, hashed)."""
    actor = f"{person.workspace}:{person.claims['sub']}"
    hashed = hashlib.sha256(f"{actor}/{thread}/{agent}".encode()).hexdigest()
    return CHECKPOINTS.query(KeyConditionExpression=Key("PK").eq(f"CHECKPOINT_{hashed}"), Select="COUNT")["Count"]


def objects(prefix: str) -> list[str]:
    return [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
            for o in page.get("Contents", [])]


def tags(key: str) -> dict:
    return {t["Key"]: t["Value"] for t in s3.get_object_tagging(Bucket=BUCKET, Key=key)["TagSet"]}


def number(value) -> int | None:
    return None if value is None else int(value)


def logged(group: str, *words: str, timeout: int = 90) -> list[str]:
    """Log lines since the run started with all these words (CloudWatch takes a few seconds)."""
    pattern = " ".join(f'"{w}"' for w in words)

    def find():
        events = logs.filter_log_events(logGroupName=group, startTime=STARTED, filterPattern=pattern)["events"]
        return [e["message"] for e in events]
    return wait_for(find, timeout=timeout, every=5) or []


# ---------- AG-UI ----------

def stream(person: Person, thread: str, run_id: str, on_event=None) -> list[dict]:
    """One request for the run: every AG-UI event AgentCore sends, as the web app reads them (on_event after each)."""
    try:
        r = requests.post(AGENT, stream=True, timeout=(10, 300), json={
            "threadId": thread, "runId": run_id, "state": {}, "messages": [], "tools": [], "context": [],
            "forwardedProps": {"tenantId": person.workspace}}, headers={
            "Authorization": f"Bearer {person.token}", "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": thread})
    except requests.RequestException as e:
        return [{"type": "RUN_ERROR", "code": "NETWORK_ERROR", "message": str(e)}]
    events = []
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            events.append(json.loads(line[5:]))
            if on_event:
                on_event(events)
    if r.status_code != 200 and not any(e.get("type") == "RUN_ERROR" for e in events):
        events.append({"type": "RUN_ERROR", "code": f"HTTP {r.status_code}", "message": r.text[:300]})
    return events


def run(person: Person, thread: str, run_id: str, on_event=None) -> list[dict]:
    """The run, followed again while AgentCore can't reach it yet (a session's microVM starting), as the page does."""
    for delay in (2, 4, 8, 16, 0):
        events = stream(person, thread, run_id, on_event)
        error = error_of(events)
        if not error or error["code"] not in ("AGENT_ERROR", "NETWORK_ERROR", "SERVICE_BUSY", "THROTTLING") \
                and not error["code"].startswith("HTTP 5"):
            return events
        time.sleep(delay)
    return events


def error_of(events: list[dict]) -> dict | None:
    return next(({"code": str(e.get("code")), "message": e.get("message")} for e in events
                 if e["type"] == "RUN_ERROR"), None)


def text_of(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def tools_of(events: list[dict]) -> list[str]:
    return [e["toolCallName"] for e in events if e["type"] == "TOOL_CALL_START"]


def results_of(events: list[dict], tool: str) -> list[str]:
    ids = {e["toolCallId"] for e in events if e["type"] == "TOOL_CALL_START" and e["toolCallName"] == tool}
    return [e["content"] for e in events if e["type"] == "TOOL_CALL_RESULT" and e["toolCallId"] in ids]


def in_order(events: list[dict]) -> bool:
    """AG-UI's own rule, which @ag-ui/client enforces: one RUN_STARTED first, RUN_FINISHED or RUN_ERROR last."""
    types = [e["type"] for e in events]
    return bool(types) and types[0] == "RUN_STARTED" and types.count("RUN_STARTED") == 1 \
        and types[-1] in ("RUN_FINISHED", "RUN_ERROR")


def send(person: Person, thread: str, content: str, **body) -> requests.Response:
    return person("POST", f"/assistant/conversations/{thread}/messages",
                  json={"id": body.pop("id", str(uuid.uuid4())), "content": content, **body})


def chat(person: Person, thread: str, content: str, **body) -> list[dict]:
    """
    Sends a message as the page does (the HTTP API saves it with its run),
    then follows the run. The first item is the run's id (not an AG-UI event).
    """
    r = send(person, thread, content, **body)
    if r.status_code != 201:
        return [{"type": "RUN_ERROR", "code": f"send {r.status_code}", "message": r.text[:300]}]
    return [{"type": "RUN_ID", "runId": r.json()["run_id"]}, *run(person, thread, r.json()["run_id"])]


def run_id_of(events: list[dict]) -> str | None:
    return next((e["runId"] for e in events if e["type"] == "RUN_ID"), None)


def answered(name: str, events: list[dict]) -> bool:
    streamed = [e for e in events if e["type"] != "RUN_ID"]
    return check(name, in_order(streamed) and not error_of(streamed) and streamed[-1]["type"] == "RUN_FINISHED",
                 error_of(streamed) or text_of(streamed)[:120])


def opened(person: Person, thread: str) -> dict:
    return person("GET", f"/assistant/conversations/{thread}").json()


def new_thread() -> str:
    return str(uuid.uuid4())


def settled(person: Person, thread: str) -> bool:
    """META as a finished run leaves it: no run, and the agent's checkpoint is of the conversation as it is."""
    item = meta(person, thread)
    heads = {agent: int(rev) for agent, rev in item.get("heads", {}).items()}
    return check("  stored: no run left, and the agent's checkpoint is of the conversation as it is (heads = rev)",
                 "run" not in item and heads.get("assistant") == number(item.get("rev")),
                 {"run": item.get("run"), "rev": item.get("rev"), "heads": heads})


# ---------- files ----------

def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A PNG of one colour (no imaging library needed)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def ticket(person: Person, name: str, data: bytes, content_type: str) -> dict | None:
    r = person("POST", "/assistant/files", json={"name": name, "content_type": content_type, "size": len(data)})
    return r.json() if check(f"POST /assistant/files ({name})", r.status_code == 201, show(r)) else None


def post_to_s3(upload: dict, name: str, data: bytes, content_type: str, **fields) -> requests.Response:
    return requests.post(upload["url"], data={**upload["fields"], **fields}, files={"file": (name, data, content_type)},
                         timeout=60)


def upload(person: Person, name: str, data: bytes, content_type: str) -> dict | None:
    """What the page does to attach a file: a presigned POST from the API, then the file straight to S3."""
    if not (got := ticket(person, name, data, content_type)):
        return None
    sent = post_to_s3(got, name, data, content_type)
    check(f"upload {name} to S3 with the presigned POST", sent.status_code in (200, 201, 204), show(sent))
    return got["file"]


def file_record(person: Person, file_id: str) -> dict:
    return items(f"{person.keys}#FILES").get(f"FILE#{file_id}", {})


# ---------- the run ----------

print(f"Assistant smoke test on {STACK} ({region}): {API}")
alice, pat, bob = Person("alice"), Person("pat"), Person("bob", joins=True)
accounts = dynamodb.Table(resources["AccountsTable"])
accounts.update_item(Key={"pk": f"TENANT#{pat.workspace}", "sk": "PROFILE"},
                     UpdateExpression="SET #plan = :pro", ExpressionAttributeNames={"#plan": "plan"},
                     ExpressionAttributeValues={":pro": "pro"})
pat.sign_in()
creds = Path(__file__).resolve().parent.parent / ".aws-sam" / "smoke-users.json"
creds.parent.mkdir(exist_ok=True)
saved = json.loads(creds.read_text()) if creds.exists() else {}
saved[f"assistant-{RUN}"] = {who: {"email": p.email, "password": p.password}
                             for who, p in (("free", alice), ("pro", pat), ("member", bob))}
creds.write_text(json.dumps(saved, indent=2))
print(f"  test users: {creds} (assistant-{RUN}); pro plan: {pat.workspaces[pat.workspace]['plan']}")

# ----- conversations: create, read, update, delete -----
print("\nA conversation: send, open, list, rename, rate, delete")
first = new_thread()
events = chat(alice, first, "Reply with exactly the word pong and nothing else.", id="q1")
answered("send: the answer streams over AG-UI, RUN_STARTED to RUN_FINISHED", events)
check("it says pong", "pong" in text_of(events).lower(), text_of(events))
stored = conversation(alice, first)
head = stored.get("META", {})
check("  stored: META and two messages, MSG#000001 and MSG#000002, nothing else",
      sorted(stored) == ["META", "MSG#000001", "MSG#000002"], sorted(stored))
check("  stored: META has the conversation's fields", head.get("thread_id") == first
      and number(head.get("message_count")) == 2 and head.get("agents") == {"assistant"}
      and head.get("question_id") == "q1" and head.get("title_source") in ("message", "model")
      and head.get("gsi1pk") == f"{alice.keys}#CONVS" and head.get("gsi1sk") == head.get("updated_at")
      and head.get("path") == [[0, 1]] and "expires_at" not in head,
      {k: head.get(k) for k in ("message_count", "agents", "question_id", "title_source", "path", "gsi1pk")})
check("  stored: the user's message as sent, the answer as streamed, each with its agent",
      stored.get("MSG#000001", {}).get("message") == {"id": "q1", "role": "user",
                                                      "content": "Reply with exactly the word pong and nothing else."}
      and stored.get("MSG#000002", {}).get("message", {}).get("role") == "assistant"
      and {stored.get(k, {}).get("agent") for k in ("MSG#000001", "MSG#000002")} == {"assistant"},
      [stored.get(k, {}).get("message") for k in ("MSG#000001", "MSG#000002")])
settled(alice, first)
check("  stored: the agent's checkpoints of it", checkpoints_of(alice, first) > 0, checkpoints_of(alice, first))
got = opened(alice, first)
check("open: GET returns the question and the answer, and no run",
      [m["role"] for m in got["messages"]] == ["user", "assistant"] and got["run_id"] is None, got.get("messages"))
listed = alice("GET", "/assistant/conversations").json()["items"]
check("list: it is in the list", [c["thread_id"] for c in listed] == [first], listed)
r = alice("PATCH", f"/assistant/conversations/{first}", json={"title": "  Smoke   renamed "})
check("rename: PATCH the title", r.ok and r.json()["title"] == "Smoke renamed", show(r))
head = meta(alice, first)
check("  stored: the title the user chose", (head.get("title"), head.get("title_source")) == ("Smoke renamed", "user"),
      (head.get("title"), head.get("title_source")))
answer_id = got["messages"][1]["id"]
rate = f"/assistant/conversations/{first}/messages/{answer_id}"
check("rate: thumbs up", alice("PATCH", rate, json={"rating": "up"}).status_code == 204)
check("  stored: the rating on the answer's record", conversation(alice, first)["MSG#000002"].get("rating") == "up")
check("rate: taken back", alice("PATCH", rate, json={"rating": None}).status_code == 204)
check("  stored: no rating", "rating" not in conversation(alice, first)["MSG#000002"])
check("rate: only an answer -> 404",
      alice("PATCH", f"/assistant/conversations/{first}/messages/q1", json={"rating": "up"}).status_code == 404)
check("another user can't open it -> 404", pat("GET", f"/assistant/conversations/{first}").status_code == 404)
check("delete: DELETE the conversation", alice("DELETE", f"/assistant/conversations/{first}").status_code == 204)
check("  stored: nothing left of it in the table", conversation(alice, first) == {})
check("  then GET -> 404, and the list is empty", alice("GET", f"/assistant/conversations/{first}").status_code == 404
      and alice("GET", "/assistant/conversations").json()["items"] == [])
check("  stored: the purge worker deleted the agent's checkpoints",
      wait_for(lambda: checkpoints_of(alice, first) == 0), checkpoints_of(alice, first))

# ----- versions -----
print("\nVersions: an edit, showing another, the agent following, an answer asked for again")
versions = new_thread()
answered("q1 answered", chat(alice, versions, "Reply with exactly the word one.", id="q1"))
answered("q2 answered", chat(alice, versions, "Reply with exactly the word two.", id="q2"))
before = conversation(alice, versions)
events = chat(alice, versions, "Reply with exactly the word three.", id="q3", replaces="q2")
answered("edit q2 (as q3): answered", events)
stored = conversation(alice, versions)
head = stored["META"]
check("  stored: the old version stays, as it was (MSG#000003 and MSG#000004)",
      all(stored.get(k) == before[k] for k in ("MSG#000001", "MSG#000002", "MSG#000003", "MSG#000004")))
check("  stored: the new version in branch 1, from position 3",
      stored.get("BRANCH#0001#MSG#000003", {}).get("message", {}).get("id") == "q3"
      and stored.get("BRANCH#0001#MSG#000004", {}).get("message", {}).get("role") == "assistant",
      sorted(k for k in stored if k.startswith("BRANCH#")))
check("  stored: META's path shows it, branch 1 continues from (0, 2), and q3 is the newest question",
      head.get("path") == [[0, 1], [1, 3]] and head["branches"]["1"]["parent"] == [0, 2]
      and number(head["branches"]["0"]["end"]) == 4 and number(head.get("message_count")) == 4
      and head.get("question_id") == "q3", {k: head.get(k) for k in ("path", "branches", "question_id")})
settled(alice, versions)
got = opened(alice, versions)
check("open: the edit shown, version 2 of 2", got["messages"][2]["id"] == "q3"
      and got["versions"].get("q3") == {"index": 2, "count": 2}, got.get("versions"))
rev, count = number(head.get("rev")), len(stored)
r = alice("POST", f"/assistant/conversations/{versions}/messages/q3/version", json={"version": 1})
check("show version 1: the old one, and what followed it", r.ok and [m["id"] for m in r.json()["messages"]][2] == "q2"
      and r.json()["versions"].get("q2") == {"index": 1, "count": 2}, show(r))
stored = conversation(alice, versions)
head = stored["META"]
check("  stored: only META changed (no message written), its path back to branch 0, rev moved on",
      len(stored) == count and head.get("path") == [[0, 1]] and number(head.get("rev")) == rev + 1
      and head.get("question_id") == "q2", {k: head.get(k) for k in ("path", "rev", "question_id")})
events = chat(alice, versions, "Which single word did you reply with just before this message? "
                               "Answer with that word only.", id="q4")
answered("a message in version 1", events)
check("the agent follows the version shown", "two" in text_of(events).lower(), text_of(events))
check("  logged: the agent started again from the history of the version shown",
      bool(logged(RUNTIME_LOGS, "Restored the agent's side", run_id_of(events) or "-")), run_id_of(events))
stored = conversation(alice, versions)
check("  stored: it goes on in branch 0 (MSG#000005, MSG#000006)",
      stored.get("MSG#000005", {}).get("message", {}).get("id") == "q4" and "MSG#000006" in stored
      and stored["META"].get("path") == [[0, 1]] and number(stored["META"]["message_count"]) == 6)
settled(alice, versions)
old_answer = stored.get("MSG#000006", {}).get("message")
events = chat(alice, versions, "again", id="q4", replaces="q4")
answered("answer q4 again", events)
stored = conversation(alice, versions)
head = stored["META"]
check("  stored: the old answer stays, the new one in branch 2 from position 6, continuing from (0, 5)",
      stored.get("MSG#000006", {}).get("message") == old_answer and "BRANCH#0002#MSG#000006" in stored
      and head.get("path") == [[0, 1], [2, 6]] and head["branches"].get("2", {}).get("parent") == [0, 5],
      {k: head.get(k) for k in ("path", "branches")})
settled(alice, versions)
got = opened(alice, versions)
new_answer = got["messages"][-1]["id"]
check("open: the new answer is version 2 of 2", got["versions"].get(new_answer) == {"index": 2, "count": 2},
      got.get("versions"))
check("rate the answer in branch 2", alice("PATCH", f"/assistant/conversations/{versions}/messages/{new_answer}",
                                           json={"rating": "down"}).status_code == 204)
check("  stored: the rating on BRANCH#0002#MSG#000006",
      conversation(alice, versions).get("BRANCH#0002#MSG#000006", {}).get("rating") == "down")
check("a version that doesn't exist -> 404",
      alice("POST", f"/assistant/conversations/{versions}/messages/{new_answer}/version",
            json={"version": 3}).status_code == 404)

# ----- Stop -----
print("\nStop in the middle of an answer")
story = new_thread()
pressed: dict = {}


def stop_after_some_text(events: list[dict]) -> None:
    """Presses Stop once some of the answer has streamed, as the page's Stop button does; notes when the run ends."""
    if "at" not in pressed and sum(e["type"] == "TEXT_MESSAGE_CONTENT" for e in events) >= 20:
        pressed["at"] = time.time()
        pressed["status"] = alice("POST", f"/assistant/conversations/{story}/runs/{pressed['run']}/cancel").status_code
    if events[-1]["type"] in ("RUN_FINISHED", "RUN_ERROR"):
        pressed["ended"] = time.time()


def tokens_this_month(person: Person) -> int:
    usage = TABLE.get_item(Key={"pk": f"TENANT#{person.workspace}", "sk": f"USAGE#{datetime.now(UTC):%Y-%m}"},
                           ConsistentRead=True).get("Item", {})
    return number(usage.get("tokens")) or 0


tokens_before = tokens_this_month(alice)
r = send(alice, story, "Write a 600-word story about a lighthouse keeper. Plain prose, no title.", id="s1")
check("send: a long answer asked for", r.status_code == 201, show(r))
pressed["run"] = r.json()["run_id"] if r.status_code == 201 else "-"
events = run(alice, story, pressed["run"], on_event=stop_after_some_text) if r.status_code == 201 else []
streamed = text_of(events)
check("Stop: POST .../runs/{run_id}/cancel -> 204", pressed.get("status") == 204, pressed.get("status"))
check("  the run ends with RUN_FINISHED {stopped: true}, within 5 seconds of Stop",
      bool(events) and events[-1]["type"] == "RUN_FINISHED" and events[-1].get("result", {}).get("stopped") is True
      and pressed.get("ended", 0) - pressed.get("at", 0) < 5,
      (events[-1] if events else None, round(pressed.get("ended", 0) - pressed.get("at", 0), 1)))
check("  only part of the story was written", 0 < len(streamed.split()) < 450, f"{len(streamed.split())} words")
kept = conversation(alice, story).get("MSG#000002", {}).get("message", {}).get("content")
check("  stored: the answer kept is exactly what streamed", kept == streamed, (len(kept or ""), len(streamed)))
settled(alice, story)
lines = logged(RUNTIME_LOGS, "Assistant answered", pressed["run"])
fields = json.loads(lines[0]) if lines else {}
check("  logged: 'Assistant answered', stopped, with the stopped call's tokens estimated",
      fields.get("stopped") is True and (number(fields.get("output_tokens")) or 0) > 0,
      {k: fields.get(k) for k in ("stopped", "input_tokens", "output_tokens")})
check("  stored: they count against the workspace's month (USAGE#<month>)", tokens_this_month(alice) > tokens_before,
      (tokens_before, tokens_this_month(alice)))
answered("the next message goes on from what was shown",
         chat(alice, story, "Continue the story from exactly where it stops. Write only the next sentence."))

# ----- an answer asked for again that fails -----
print("\nAn answer asked for again that fails before writing anything")
failing = new_thread()
answered("c1 answered", chat(alice, failing, "Reply with exactly the word green.", id="c1"))
before, checkpoints_before = conversation(alice, failing), checkpoints_of(alice, failing)
r = send(alice, failing, "again", id="c1", replaces="c1")
check("answer c1 again: saved with its run", r.status_code == 201, show(r))
# The fault, written straight to the table: before the runtime picks the run up, its agent is gone (as after
# a deploy that removed it), so the run fails in the runtime before the model writes anything.
TABLE.update_item(Key={"pk": f"{alice.keys}#CONV#{failing}", "sk": "META"}, UpdateExpression="SET #run.#agent = :gone",
                  ExpressionAttributeNames={"#run": "run", "#agent": "agent"},
                  ExpressionAttributeValues={":gone": "removed-agent"})
events = run(alice, failing, r.json()["run_id"]) if r.status_code == 201 else []
check("  the run fails with RUN_ERROR, having written nothing", error_of(events) is not None and not text_of(events),
      error_of(events) or text_of(events)[:120])
stored = conversation(alice, failing)
head = stored.get("META", {})
check("  stored: the empty version is gone: no BRANCH# item, the path back to branch 0, 2 messages, no run",
      not [k for k in stored if k.startswith("BRANCH#")] and head.get("path") == [[0, 1]]
      and number(head.get("message_count")) == 2 and head.get("run") is None,
      {k: head.get(k) for k in ("path", "branches", "message_count", "run")})
got = opened(alice, failing)
check("open: the old answer is shown again, with no versions",
      [m["id"] for m in got["messages"]] == ["c1", before.get("MSG#000002", {}).get("message", {}).get("id")]
      and not got.get("versions") and got.get("run_id") is None, (got.get("messages"), got.get("versions")))
heads = {agent: int(rev) for agent, rev in head.get("heads", {}).items()}
check("  stored: the agent's checkpoint untouched, and heads not moved (the failed run kept nothing)",
      checkpoints_of(alice, failing) == checkpoints_before and heads.get("assistant") != number(head.get("rev")),
      {"checkpoints": (checkpoints_before, checkpoints_of(alice, failing)), "heads": heads, "rev": head.get("rev")})
events = chat(alice, failing, "Repeat your previous reply exactly, nothing else.", id="c2")
answered("the next message", events)
check("  answered from what the user sees", "green" in text_of(events).lower(), text_of(events))
check("  logged: the agent started again from the history",
      bool(logged(RUNTIME_LOGS, "Restored the agent's side", run_id_of(events) or "-")), run_id_of(events))
settled(alice, failing)

# ----- a long conversation -----
print("\nA long conversation (250 messages: the 248 after its first answer written straight to the table)")
long = new_thread()
answered("its first answer", chat(alice, long, "Reply with exactly: ok", id="m1"))
pk = f"{alice.keys}#CONV#{long}"
with TABLE.batch_writer() as batch:
    for n in range(3, 251):
        batch.put_item(Item={"pk": pk, "sk": f"MSG#{n:06d}", "agent": "assistant",
                             "message": {"id": f"m{n}", "role": "assistant", "content": f"Part {n}"}})
TABLE.update_item(Key={"pk": pk, "sk": "META"}, UpdateExpression="SET message_count = :n",
                  ExpressionAttributeValues={":n": 250})
got = opened(alice, long)
check("it opens at its latest 200, with an earlier_cursor", len(got["messages"]) == 200
      and got["messages"][-1]["id"] == "m250" and got["earlier_cursor"], len(got["messages"]))
cursor = got["earlier_cursor"]
r = alice("GET", f"/assistant/conversations/{long}/messages", params={"before": cursor})
page = r.json() if r.ok else {}
check("GET .../messages?before= reads the 50 before, and nothing is left",
      r.ok and len(page["messages"]) == 50 and page["messages"][0]["id"] == "m1" and page["earlier_cursor"] is None,
      show(r))
r = alice("GET", f"/assistant/conversations/{long}/messages", params={"before": cursor, "limit": 20})
check("?limit=20 reads the 20 just before", r.ok and [m["id"] for m in r.json()["messages"]][0] == "m31", show(r))
answered("the conversation grows", chat(alice, long, "Reply with exactly: more"))
r = alice("GET", f"/assistant/conversations/{long}/messages", params={"before": cursor})
check("the cursor still reads the same page", r.ok and len(r.json()["messages"]) == 50, show(r))
check("a cursor it didn't give -> 400",
      alice("GET", f"/assistant/conversations/{long}/messages", params={"before": "nope"}).status_code == 400)
answered("another tab edits m1", chat(alice, long, "Reply with exactly: edited", id="m0", replaces="m1"))
r = alice("GET", f"/assistant/conversations/{long}/messages", params={"before": cursor})
check("the old cursor, now of another version -> 409", r.status_code == 409, show(r))

# ----- memory -----
print("\nMemory: add, read, edit, the agent saving and recalling, off and on, the limit, delete")
memory_pk = f"{alice.keys}#MEMORY"
r = alice("POST", "/assistant/memories", json={"text": "Prefers answers in one short sentence"})
check("add: POST /assistant/memories", r.status_code == 201, show(r))
memory = r.json()
item = items(memory_pk).get(f"MEM#{memory['memory_id']}", {})
check("  stored: MEM#<id> with its text and times, no conversation (the user wrote it)",
      item.get("text") == "Prefers answers in one short sentence" and item.get("created_at") == item.get("updated_at")
      and "thread_id" not in item, item)
r = alice("POST", "/assistant/memories", json={"text": "prefers ANSWERS in one short sentence"})
check("the same text again is the same memory", r.ok and r.json()["memory_id"] == memory["memory_id"]
      and len([k for k in items(memory_pk) if k.startswith("MEM#")]) == 1, show(r))
time.sleep(1)
r = alice("PATCH", f"/assistant/memories/{memory['memory_id']}", json={"text": "Prefers short answers"})
check("edit: PATCH /assistant/memories/{id}", r.ok and r.json()["text"] == "Prefers short answers", show(r))
item = items(memory_pk).get(f"MEM#{memory['memory_id']}", {})
check("  stored: the new text, updated_at moved on", item.get("text") == "Prefers short answers"
      and item.get("updated_at", "") > item.get("created_at", ""), item)
remembered = new_thread()
events = chat(alice, remembered, "Please remember this for later conversations: my favourite fruit is the kumquat.")
answered("asked to remember something", events)
check("the agent saves it with remember", "remember" in tools_of(events), tools_of(events))
saved_by_agent = [m for k, m in items(memory_pk).items() if k.startswith("MEM#") and "kumquat" in m["text"].lower()]
check("  stored: the agent's memory, with the conversation it was saved in",
      len(saved_by_agent) == 1 and saved_by_agent[0].get("thread_id") == remembered, saved_by_agent)
memories = alice("GET", "/assistant/memories").json()
check("read: GET /assistant/memories has both, on, limit 100", len(memories["items"]) == 2
      and memories["enabled"] is True and memories["limit"] == 100, [m["text"] for m in memories["items"]])
events = chat(alice, new_thread(), "What is my favourite fruit? Answer with one word.")
answered("a new conversation", events)
check("it knows what it remembered, in another conversation", "kumquat" in text_of(events).lower(), text_of(events))
r = alice("PATCH", "/assistant/memories", json={"enabled": False})
check("off: PATCH /assistant/memories {enabled: false}", r.ok and r.json()["enabled"] is False, show(r))
check("  stored: SETTINGS enabled false", items(memory_pk).get("SETTINGS", {}).get("enabled") is False)
events = chat(alice, new_thread(), "What is my favourite fruit? If you don't know, answer with the word unknown.")
answered("a new conversation with memory off", events)
check("off, it neither knows nor saves",
      "kumquat" not in text_of(events).lower() and "remember" not in tools_of(events), text_of(events))
alice("PATCH", "/assistant/memories", json={"enabled": True})
check("  stored: SETTINGS enabled true again", items(memory_pk).get("SETTINGS", {}).get("enabled") is True)
check("delete one: DELETE /assistant/memories/{id}",
      alice("DELETE", f"/assistant/memories/{memory['memory_id']}").status_code == 204)
check("  stored: its record is gone", f"MEM#{memory['memory_id']}" not in items(memory_pk))
check("  deleting it again -> 404", alice("DELETE", f"/assistant/memories/{memory['memory_id']}").status_code == 404)
for n in range(99):
    alice("POST", "/assistant/memories", json={"text": f"Smoke fact number {n}"})
r = alice("POST", "/assistant/memories", json={"text": "One too many"})
check("the 101st -> 409", r.status_code == 409, show(r))
check("  stored: 100 MEM# records", len([k for k in items(memory_pk) if k.startswith("MEM#")]) == 100)
check("forget everything: DELETE /assistant/memories", alice("DELETE", "/assistant/memories").status_code == 204)
check("  stored: no MEM# left, SETTINGS kept", list(items(memory_pk)) == ["SETTINGS"], sorted(items(memory_pk)))
lines = logged(RUNTIME_LOGS, "Memory saved") + logged(APP_LOGS, "Memory saved")
check("  logged: 'Memory saved' lines name the memory, never its text",
      bool(lines) and not any("kumquat" in line.lower() or "short answers" in line.lower() for line in lines),
      f"{len(lines)} lines")

# ----- files -----
print("\nFiles: upload, attach, what the model reads, files the agent writes, links, delete")
rules = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]


def tagged_pending(rule: dict) -> bool:
    """The rule's filter is the pending tag alone (S3 may give it back as {"And": {"Prefix": "", "Tags": [...]}})."""
    found = rule.get("Filter", {})
    tags_ = [found["Tag"]] if "Tag" in found else found.get("And", {}).get("Tags", [])
    return tags_ == [{"Key": "pending", "Value": "true"}] and not found.get("And", {}).get("Prefix")


check("the bucket deletes uploads still tagged pending after a day",
      any(rule.get("Status") == "Enabled" and rule.get("Expiration", {}).get("Days") == 1 and tagged_pending(rule)
          for rule in rules), rules)
blocked = s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"]
check("the bucket is private", all(blocked.values()), blocked)
check("an .exe can't be attached",
      alice("POST", "/assistant/files", json={"name": "setup.exe", "content_type": "application/x-msdownload",
                                              "size": 10}).status_code == 400)
check("an image over 5 MB can't be attached",
      alice("POST", "/assistant/files", json={"name": "big.png", "content_type": "image/png",
                                              "size": 6 * 1024 * 1024}).status_code == 400)
hour = {"pk": f"TENANT#{alice.workspace}",
        "sk": f"USER#{alice.claims['sub']}#UPLOADS#{datetime.now(UTC):%Y-%m-%dT%H}"}
uploads_before = number(TABLE.get_item(Key=hour, ConsistentRead=True).get("Item", {}).get("messages")) or 0
NOTE = b"The secret code word is marmalade-77."
got = ticket(alice, "smoke-note.txt", NOTE, "text/plain")
note = got and got["file"]
if got:
    record = file_record(alice, note["file_id"])
    check("  stored: FILE#<id> pending, with a TTL a day away, no conversation yet",
          record.get("status") == "pending" and record.get("source") == "upload" and record.get("kind") == "text"
          and abs(number(record.get("expires_at")) - (time.time() + 86400)) < 600 and "thread_id" not in record
          and [number(v["version"]) for v in record.get("versions", [])] == [1], record)
    counted = number(TABLE.get_item(Key=hour, ConsistentRead=True).get("Item", {}).get("messages"))
    check("  stored: the upload counted against the hour (USER#<u>#UPLOADS#<hour>)", counted == uploads_before + 1,
          (uploads_before, counted))
    check("S3 refuses a file of another size",
          post_to_s3(got, "smoke-note.txt", NOTE + b"!", "text/plain").status_code in (400, 403))
    check("S3 refuses another type",
          post_to_s3(got, "smoke-note.txt", NOTE, "text/html", **{"Content-Type": "text/html"})
          .status_code in (400, 403))
    check("S3 refuses the file without its pending tag",
          post_to_s3(got, "smoke-note.txt", NOTE, "text/plain", tagging="<Tagging><TagSet/></Tagging>")
          .status_code in (400, 403))
    sent = post_to_s3(got, "smoke-note.txt", NOTE, "text/plain")
    check("S3 takes the file as asked for", sent.status_code == 204, show(sent))
    key = f"{alice.prefix}{note['file_id']}/1"
    check("  stored: <tenant>/<user>/<file_id>/1, tagged pending=true",
          s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == NOTE and tags(key) == {"pending": "true"})
image = upload(alice, "smoke-red.png", png(64, 64, (220, 20, 20)), "image/png")
if note and image:
    attached = new_thread()
    events = chat(alice, attached, "What is the secret code word in the attached note, and what colour fills the "
                                   "attached image? Answer as: word, colour.", id="f1",
                  attachments=[note["file_id"], image["file_id"]])
    answered("send a message with a text file and an image", events)
    answer = text_of(events).lower()
    check("the model read the text file", "marmalade-77" in answer, answer)
    check("the model saw the image", "red" in answer, answer)
    for file in (note, image):
        record = file_record(alice, file["file_id"])
        check(f"  stored: {file['name']} kept: ready, no TTL, in that conversation",
              record.get("status") == "ready" and "expires_at" not in record and record.get("thread_id") == attached,
              {k: record.get(k) for k in ("status", "expires_at", "thread_id")})
        check("  stored: its object's pending tag is gone", tags(f"{alice.prefix}{file['file_id']}/1") == {})
    content = conversation(alice, attached).get("MSG#000001", {}).get("message", {}).get("content", [])
    check("  stored: the message as AG-UI parts, each file by its id (a file source) with its name and size",
          [p["type"] for p in content] == ["text", "document", "image"]
          and content[1]["source"] == {"type": "file", "value": note["file_id"], "mimeType": "text/plain"}
          and content[2]["metadata"]["name"] == "smoke-red.png" and number(content[1]["metadata"]["size"]) == len(NOTE),
          content)
    links = alice("GET", f"/assistant/files/{note['file_id']}").json()
    got_note = requests.get(links["download_url"], timeout=30)
    check("the download link: the file, as an attachment with its name, no view link for text",
          got_note.content == NOTE and "attachment" in got_note.headers.get("Content-Disposition", "")
          and "smoke-note.txt" in got_note.headers.get("Content-Disposition", "") and links["view_url"] is None,
          dict(got_note.headers))
    links = alice("GET", f"/assistant/files/{image['file_id']}").json()
    shown = requests.get(links["view_url"], headers={"Origin": out["WebUrl"]}, timeout=30)
    check("the image's view link: shown inline as image/png, with CORS for the app",
          shown.ok and shown.headers.get("Content-Type") == "image/png"
          and shown.headers.get("Content-Disposition") == "inline"
          and shown.headers.get("Access-Control-Allow-Origin") in ("*", out["WebUrl"]), dict(shown.headers))
    check("another user can't read them -> 404", pat("GET", f"/assistant/files/{note['file_id']}").status_code == 404)
never = upload(alice, "never-sent.txt", b"x", "text/plain")
listed = alice("GET", "/assistant/files", params={"source": "upload"}).json()["items"]
check("list: the two sent, not the one never sent", sorted(f["name"] for f in listed)
      == ["smoke-note.txt", "smoke-red.png"], [f["name"] for f in listed])
if never:
    check("  stored: the one never sent stays pending until its TTL",
          file_record(alice, never["file_id"]).get("status") == "pending")

written = new_thread()
events = chat(alice, written, "Use the write_file tool to save a file named smoke-report.md whose content is exactly "
                              "'# Smoke report' and nothing else.")
answered("asked to write a file", events)
made = [json.loads(r) for r in results_of(events, "write_file") if r.startswith("{")]
check("write_file saved it, and its result is the file as JSON", made and made[0]["name"] == "smoke-report.md",
      results_of(events, "write_file"))
if made:
    report = made[0]["file_id"]
    record = file_record(alice, report)
    files = items(f"{alice.keys}#FILES")
    check("  stored: FILE#<id> made by the assistant, ready, in that conversation, version 1",
          record.get("source") == "assistant" and record.get("agent") == "assistant" and record.get("status") == "ready"
          and record.get("thread_id") == written and len(record.get("versions", [])) == 1, record)
    check("  stored: NAMED#<conversation>#smoke-report.md points at it",
          files.get(f"NAMED#{written}#smoke-report.md", {}).get("file_id") == report)
    events = chat(alice, written, "Save smoke-report.md again with write_file, now with the content "
                                  "'# Smoke report v2'.")
    answered("asked to save it again", events)
    record = file_record(alice, report)
    check("  stored: the same record, two versions, the newest's size",
          [number(v["version"]) for v in record.get("versions", [])] == [1, 2]
          and number(record.get("size")) == number(record["versions"][1]["size"]), record.get("versions"))
    bodies = {key.rsplit("/", 1)[1]: s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
              for key in objects(f"{alice.prefix}{report}/")}
    check("  stored: one object per version", sorted(bodies) == ["1", "2"] and b"v2" in bodies.get("2", b"")
          and b"v2" not in bodies.get("1", b"v2"), {k: v[:30] for k, v in bodies.items()})
    newest = requests.get(alice("GET", f"/assistant/files/{report}").json()["download_url"], timeout=30)
    first_version = requests.get(alice("GET", f"/assistant/files/{report}", params={"version": 1})
                                 .json()["download_url"], timeout=30)
    check("read: the links give the newest version, and version 1", b"v2" in newest.content
          and b"v2" not in first_version.content)
    check("version 3 -> 404", alice("GET", f"/assistant/files/{report}", params={"version": 3}).status_code == 404)
    listed = alice("GET", "/assistant/files", params={"source": "assistant", "kind": "text"}).json()["items"]
    check("list: ?source=assistant&kind=text has it", [f["file_id"] for f in listed] == [report], listed)
    check("delete: DELETE /assistant/files/{id}", alice("DELETE", f"/assistant/files/{report}").status_code == 204)
    files = items(f"{alice.keys}#FILES")
    check("  stored: its record and its name are gone, and both objects",
          f"FILE#{report}" not in files and f"NAMED#{written}#smoke-report.md" not in files
          and objects(f"{alice.prefix}{report}/") == [], sorted(files))
    check("  then GET -> 404, DELETE again -> 404", alice("GET", f"/assistant/files/{report}").status_code == 404
          and alice("DELETE", f"/assistant/files/{report}").status_code == 404)

# ----- the sandbox's files -----
print("\nThe sandbox's files (Pro)")
csv = upload(pat, "smoke-sales.csv", b"item,amount\ntea,12\ncoffee,30\ncake,8\n", "text/csv")
if csv:
    sandbox = new_thread()
    events = chat(pat, sandbox, "Use open_file to put the attached CSV in the sandbox, then use bash to run Python "
                                "that adds up its amount column. Reply with the total only.",
                  attachments=[csv["file_id"]])
    answered("asked to work on an attached file in the sandbox", events)
    check("open_file put it in the sandbox", any("files/smoke-sales.csv" in r for r in results_of(events, "open_file")),
          results_of(events, "open_file"))
    check("the total is right", "50" in text_of(events), text_of(events))
    events = chat(pat, sandbox, "In the sandbox, use Python with matplotlib to draw a bar chart of that CSV's amounts, "
                                "save it as chart.png, then save it for me with save_file.")
    answered("asked for a chart", events)
    charts = [f for f in pat("GET", "/assistant/files", params={"kind": "image"}).json()["items"]
              if f["source"] == "assistant"]
    check("save_file saved the chart from the sandbox", charts and charts[0]["content_type"] == "image/png",
          (tools_of(events), results_of(events, "save_file")))
    if charts:
        body = s3.get_object(Bucket=BUCKET, Key=f"{pat.prefix}{charts[0]['file_id']}/1")["Body"].read()
        check("  stored: the chart's object is a PNG", body.startswith(b"\x89PNG"), len(body))

# ----- a member who leaves -----
print("\nA member who leaves: the purge worker deletes their conversations, memories, files and checkpoints")
r = alice("POST", "/users/invite", json={"email": bob.email, "role": "member"})
check("alice invites bob", r.status_code == 201, show(r))
r = bob("POST", f"/invitations/{alice.workspace}/accept")
check("bob accepts", r.ok, show(r))
bob.sign_in()
bob.workspace = alice.workspace
bobs = new_thread()
answered("bob chats", chat(bob, bobs, "Reply with exactly: hello"))
bob("POST", "/assistant/memories", json={"text": "Bob likes green tea"})
bobs_file = upload(bob, "bob.txt", b"Bob's notes", "text/plain")
if bobs_file:
    answered("bob sends a file", chat(bob, bobs, "Here is a file. Reply with exactly: got it",
                                      attachments=[bobs_file["file_id"]]))
check("  stored: bob has a conversation, a memory, a file and checkpoints",
      bool(conversation(bob, bobs) and items(f"{bob.keys}#MEMORY") and items(f"{bob.keys}#FILES")
           and objects(bob.prefix) and checkpoints_of(bob, bobs) > 0))
alices = {"files": len(items(f"{alice.keys}#FILES")), "objects": len(objects(alice.prefix))}
bob_keys, bob_prefix = bob.keys, bob.prefix
r = alice("DELETE", f"/users/{bob.claims['sub']}")
check("alice removes bob", r.status_code == 204, show(r))
gone = wait_for(lambda: not items(f"{bob_keys}#CONV#{bobs}") and not items(f"{bob_keys}#MEMORY")
                and not items(f"{bob_keys}#FILES") and not objects(bob_prefix), timeout=240, every=10)
check("  stored: bob's conversation, memories, file records and objects are gone", bool(gone),
      {"conversation": len(items(f"{bob_keys}#CONV#{bobs}")), "memories": len(items(f"{bob_keys}#MEMORY")),
       "files": len(items(f"{bob_keys}#FILES")), "objects": len(objects(bob_prefix))})
check("  stored: and his checkpoints", wait_for(lambda: checkpoints_of(bob, bobs) == 0, timeout=60))
check("  stored: alice's files are untouched", {"files": len(items(f"{alice.keys}#FILES")),
                                                "objects": len(objects(alice.prefix))} == alices)

print()
if failures:
    print(f"{len(failures)} failed: " + "; ".join(failures))
    sys.exit(1)
print("All assistant checks passed.")
