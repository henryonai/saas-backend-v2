# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3", "pycognito", "requests"]
# ///
"""
Smoke test for the assistant on a deployed stack, run after sam deploy with
its code (scripts/package-assistant.py). With the real model, AgentCore and
S3, it checks what the unit tests fake, printing PASS/FAIL per check:

  a conversation through the HTTP API and AgentCore's AG-UI stream; a long
  conversation read back past its latest 200 messages; versions (an edit, an
  answer asked for again, showing another, the agent following the version
  shown); memory (the API, the agent saving and recalling, memory off);
  files (uploads with the presigned POST, an image and a text file sent to
  the model, files the agent writes, versions, links, deletes); and, on a
  Pro workspace, the sandbox's files (open_file, save_file).

    uv run scripts/smoke-assistant.py [--stack saas-dev]

Answers come from the stack's real model, so a check about what it says can
fail now and then on a model that ignores an instruction: read the detail.
Uses your AWS credentials to create two test users (one's workspace is set to
Pro in the accounts table), so run it against dev, not prod. Their logins go
to .aws-sam/smoke-users.json (gitignored) so you can sign in with them and see
what the run left; they are never printed. It costs a few cents of model
tokens and one sandbox session.
"""
import argparse
import base64
import json
import secrets
import struct
import sys
import time
import uuid
import zlib
from pathlib import Path

import boto3
import requests
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
RUN = int(time.time())
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({str(detail)[:300]})" if detail != "" else ""), flush=True)
    if not ok:
        failures.append(name)
    return ok


def show(r: requests.Response) -> str:
    return f"{r.status_code} {r.text[:200]}"


class Person:
    """A test user in the user pool, signed in with SRP, with a workspace of their own."""

    def __init__(self, name: str):
        self.email = f"smoke-assistant-{name}+{RUN}@example.com"
        self.password = "Sm0ke!" + secrets.token_urlsafe(18)
        idp.admin_create_user(UserPoolId=POOL, Username=self.email, MessageAction="SUPPRESS",
                              UserAttributes=[{"Name": "email", "Value": self.email},
                                              {"Name": "email_verified", "Value": "true"}])
        idp.admin_set_user_password(UserPoolId=POOL, Username=self.email, Password=self.password, Permanent=True)
        self.sign_in()
        r = self("POST", "/tenants", json={"name": f"Smoke assistant {name}"})
        assert r.status_code == 201, show(r)
        self.sign_in()   # the token now carries the workspace

    def sign_in(self) -> "Person":
        self.cognito = Cognito(POOL, CLIENT, user_pool_region=region, username=self.email)
        self.cognito.authenticate(password=self.password)
        return self

    @property
    def token(self) -> str:
        return self.cognito.id_token

    @property
    def claims(self) -> dict:
        payload = self.token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    def __call__(self, method: str, path: str, **kw) -> requests.Response:
        return requests.request(method, API + path, headers={"Authorization": f"Bearer {self.token}"}, timeout=30, **kw)


# ---------- AG-UI ----------

def stream(person: Person, thread: str, run_id: str) -> list[dict]:
    """One request for the run: every AG-UI event AgentCore sends, as the web app reads them."""
    try:
        r = requests.post(AGENT, stream=True, timeout=(10, 300), json={
            "threadId": thread, "runId": run_id, "state": {}, "messages": [], "tools": [], "context": [],
            "forwardedProps": {}}, headers={
            "Authorization": f"Bearer {person.token}", "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": thread})
    except requests.RequestException as e:
        return [{"type": "RUN_ERROR", "code": "NETWORK_ERROR", "message": str(e)}]
    events = [json.loads(line[5:]) for line in r.iter_lines(decode_unicode=True) if line and line.startswith("data:")]
    if r.status_code != 200 and not any(e.get("type") == "RUN_ERROR" for e in events):
        events.append({"type": "RUN_ERROR", "code": f"HTTP {r.status_code}", "message": r.text[:300]})
    return events


def run(person: Person, thread: str, run_id: str) -> list[dict]:
    """The run, followed again while AgentCore can't reach it yet (a session's microVM starting), as the page does."""
    for delay in (2, 4, 8, 16, 0):
        events = stream(person, thread, run_id)
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
    """Sends a message as the page does (the HTTP API saves it with its run), then follows the run."""
    r = send(person, thread, content, **body)
    if r.status_code != 201:
        return [{"type": "RUN_ERROR", "code": f"send {r.status_code}", "message": r.text[:300]}]
    return run(person, thread, r.json()["run_id"])


def answered(name: str, events: list[dict]) -> bool:
    return check(name, in_order(events) and not error_of(events) and events[-1]["type"] == "RUN_FINISHED",
                 error_of(events) or text_of(events)[:120])


def opened(person: Person, thread: str) -> dict:
    return person("GET", f"/assistant/conversations/{thread}").json()


def new_thread() -> str:
    return str(uuid.uuid4())


# ---------- files ----------

def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A PNG of one colour (no imaging library needed)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def upload(person: Person, name: str, data: bytes, content_type: str) -> dict | None:
    """What the page does to attach a file: a presigned POST from the API, then the file straight to S3."""
    r = person("POST", "/assistant/files", json={"name": name, "content_type": content_type, "size": len(data)})
    if not check(f"POST /assistant/files ({name})", r.status_code == 201, show(r)):
        return None
    ticket = r.json()
    sent = requests.post(ticket["url"], data=ticket["fields"], files={"file": (name, data, content_type)}, timeout=60)
    check(f"upload {name} to S3 with the presigned POST", sent.status_code in (200, 201, 204), show(sent))
    return ticket["file"]


# ---------- the run ----------

print(f"Assistant smoke test on {STACK} ({region}): {API}")
alice, pat = Person("alice"), Person("pat")
accounts = dynamodb.Table(resources["AccountsTable"])
accounts.update_item(Key={"pk": f"TENANT#{pat.claims['tenant_id']}", "sk": "PROFILE"},
                     UpdateExpression="SET #plan = :pro", ExpressionAttributeNames={"#plan": "plan"},
                     ExpressionAttributeValues={":pro": "pro"})
pat.sign_in()
creds = Path(__file__).resolve().parent.parent / ".aws-sam" / "smoke-users.json"
creds.parent.mkdir(exist_ok=True)
saved = json.loads(creds.read_text()) if creds.exists() else {}
saved[f"assistant-{RUN}"] = {who: {"email": p.email, "password": p.password} for who, p in (("free", alice),
                                                                                         ("pro", pat))}
creds.write_text(json.dumps(saved, indent=2))
print(f"  test users: {creds} (assistant-{RUN}); pro plan: {pat.claims.get('plan')}")

print("\nA conversation")
first = new_thread()
events = chat(alice, first, "Reply with exactly the word pong and nothing else.", id="q1")
answered("the answer streams over AG-UI, RUN_STARTED to RUN_FINISHED", events)
check("it says pong", "pong" in text_of(events).lower(), text_of(events))
conversation = opened(alice, first)
check("the conversation keeps the question and the answer, and no run",
      [m["role"] for m in conversation["messages"]] == ["user", "assistant"] and conversation["run_id"] is None,
      conversation.get("messages"))
check("a short conversation has nothing earlier", conversation.get("earlier_cursor") is None)
listed = alice("GET", "/assistant/conversations").json()["items"]
check("it is in the list", [c["thread_id"] for c in listed] == [first], listed)

print("\nVersions")
events = chat(alice, first, "Reply with exactly the word ping and nothing else.", id="q2", replaces="q1")
answered("an edit is answered", events)
conversation = opened(alice, first)
check("the edit is shown, version 2 of 2", conversation["messages"][0]["id"] == "q2"
      and conversation["versions"].get("q2") == {"index": 2, "count": 2}, conversation.get("versions"))
r = alice("POST", f"/assistant/conversations/{first}/messages/q2/version", json={"version": 1})
check("POST .../version shows version 1, and what followed it",
      r.ok and r.json()["messages"][0]["id"] == "q1" and "pong" in r.json()["messages"][1]["content"].lower(), show(r))
events = chat(alice, first, "Which single word did you reply with just before this message? "
                            "Answer with that word only.")
answered("the version shown goes on", events)
check("the agent follows the version shown (rebuilt from its history)", "pong" in text_of(events).lower(),
      text_of(events))
conversation = opened(alice, first)
question = conversation["messages"][-2]["id"]
events = chat(alice, first, "again", id=question, replaces=question)
answered("an answer asked for again", events)
conversation = opened(alice, first)
answer = conversation["messages"][-1]["id"]
check("the new answer is version 2 of 2", conversation["versions"].get(answer) == {"index": 2, "count": 2},
      conversation.get("versions"))

print("\nA long conversation (250 messages, written straight to the table)")
long = new_thread()
answered("its first answer", chat(alice, long, "Reply with exactly: ok", id="m1"))
table = dynamodb.Table(resources["AssistantTable"])
pk = f"TENANT#{alice.claims['tenant_id']}#USER#{alice.claims['sub']}#CONV#{long}"
with table.batch_writer() as batch:
    for n in range(3, 251):
        batch.put_item(Item={"pk": pk, "sk": f"MSG#{n:06d}", "agent": "assistant",
                             "message": {"id": f"m{n}", "role": "assistant", "content": f"Part {n}"}})
table.update_item(Key={"pk": pk, "sk": "META"}, UpdateExpression="SET message_count = :n",
                  ExpressionAttributeValues={":n": 250})
conversation = opened(alice, long)
check("it opens at its latest 200", len(conversation["messages"]) == 200
      and conversation["messages"][-1]["id"] == "m250" and conversation["earlier_cursor"],
      len(conversation["messages"]))
r = alice("GET", f"/assistant/conversations/{long}/messages", params={"before": conversation["earlier_cursor"]})
page = r.json() if r.ok else {}
check("GET .../messages?before= reads the 50 before, and nothing is left",
      r.ok and len(page["messages"]) == 50 and page["messages"][0]["id"] == "m1" and page["earlier_cursor"] is None,
      show(r))

print("\nMemory")
r = alice("POST", "/assistant/memories", json={"text": "Prefers answers in one short sentence"})
check("POST /assistant/memories", r.status_code == 201, show(r))
memory = r.json()
events = chat(alice, new_thread(), "Please remember this for later conversations: my favourite fruit is the kumquat.")
answered("asked to remember something", events)
check("the agent saves it with remember", "remember" in tools_of(events), tools_of(events))
memories = alice("GET", "/assistant/memories").json()
check("GET /assistant/memories has both", len(memories["items"]) == 2 and memories["enabled"] is True,
      [m["text"] for m in memories["items"]])
events = chat(alice, new_thread(), "What is my favourite fruit? Answer with one word.")
answered("a new conversation", events)
check("it knows what it remembered, in another conversation", "kumquat" in text_of(events).lower(), text_of(events))
r = alice("PATCH", "/assistant/memories", json={"enabled": False})
check("memory off", r.ok and r.json()["enabled"] is False, show(r))
events = chat(alice, new_thread(), "What is my favourite fruit? If you don't know, answer with the word unknown.")
answered("a new conversation with memory off", events)
check("off, it doesn't know", "kumquat" not in text_of(events).lower() and "remember" not in tools_of(events),
      text_of(events))
alice("PATCH", "/assistant/memories", json={"enabled": True})
r = alice("PATCH", f"/assistant/memories/{memory['memory_id']}", json={"text": "Prefers short answers"})
check("PATCH /assistant/memories/{id}", r.ok and r.json()["text"] == "Prefers short answers", show(r))
check("DELETE /assistant/memories/{id}",
      alice("DELETE", f"/assistant/memories/{memory['memory_id']}").status_code == 204)
check("DELETE /assistant/memories (forget everything)", alice("DELETE", "/assistant/memories").status_code == 204)
check("nothing is left", alice("GET", "/assistant/memories").json()["items"] == [])

print("\nFiles")
check("an .exe can't be attached",
      alice("POST", "/assistant/files", json={"name": "setup.exe", "content_type": "application/x-msdownload",
                                              "size": 10}).status_code == 400)
check("an image over 5 MB can't be attached",
      alice("POST", "/assistant/files", json={"name": "big.png", "content_type": "image/png",
                                              "size": 6 * 1024 * 1024}).status_code == 400)
note = upload(alice, "smoke-note.txt", b"The secret code word is marmalade-77.", "text/plain")
image = upload(alice, "smoke-red.png", png(64, 64, (220, 20, 20)), "image/png")
if note and image:
    thread = new_thread()
    events = chat(alice, thread, "What is the secret code word in the attached note, and what colour fills the "
                                 "attached image? Answer as: word, colour.",
                  attachments=[note["file_id"], image["file_id"]])
    answered("a message with a text file and an image", events)
    answer = text_of(events).lower()
    check("the model read the text file", "marmalade-77" in answer, answer)
    check("the model saw the image", "red" in answer, answer)
    question = opened(alice, thread)["messages"][0]
    check("the message keeps them as AG-UI image and document parts",
          [p["type"] for p in question["content"]] == ["text", "document", "image"], question["content"])
    bucket = resources["AssistantFilesBucket"]
    key = f"{alice.claims['tenant_id']}/{alice.claims['sub']}/{note['file_id']}/1"
    check("sent, the upload is kept (its pending tag is gone)",
          s3.get_object_tagging(Bucket=bucket, Key=key)["TagSet"] == [])
upload(alice, "never-sent.txt", b"x", "text/plain")   # never sent with a message
uploads = alice("GET", "/assistant/files", params={"source": "upload"}).json()["items"]
check("the list has the two sent, not the one never sent", sorted(f["name"] for f in uploads)
      == ["smoke-note.txt", "smoke-red.png"], [f["name"] for f in uploads])

thread = new_thread()
events = chat(alice, thread, "Use the write_file tool to save a file named smoke-report.md whose content is exactly "
                             "'# Smoke report' and nothing else.")
answered("asked to write a file", events)
saved = [json.loads(r) for r in results_of(events, "write_file") if r.startswith("{")]
check("write_file saved it, and its result is the file as JSON", saved and saved[0]["name"] == "smoke-report.md",
      results_of(events, "write_file"))
events = chat(alice, thread, "Save smoke-report.md again with write_file, now with the content '# Smoke report v2'.")
answered("asked to save it again", events)
made = alice("GET", "/assistant/files", params={"source": "assistant"}).json()["items"]
report = next((f for f in made if f["name"] == "smoke-report.md"), None)
check("the same name in the same conversation is a new version",
      report is not None and len(report["versions"]) == 2 and report["agent"] == "assistant", made)
if report:
    detail = alice("GET", f"/assistant/files/{report['file_id']}").json()
    body = requests.get(detail["download_url"], timeout=30)
    check("its download link gives the newest version", body.ok and "v2" in body.text, body.text[:100])
    first_version = alice("GET", f"/assistant/files/{report['file_id']}", params={"version": 1}).json()
    check("and version 1 is still there", "v2" not in requests.get(first_version["download_url"], timeout=30).text)
    check("DELETE /assistant/files/{id}", alice("DELETE", f"/assistant/files/{report['file_id']}").status_code == 204)
    check("then it's gone", alice("GET", f"/assistant/files/{report['file_id']}").status_code == 404)
if image:
    detail = alice("GET", f"/assistant/files/{image['file_id']}").json()
    shown = requests.get(detail.get("view_url") or "", timeout=30) if detail.get("view_url") else None
    check("an image has a link that shows it", shown is not None and shown.ok
          and shown.headers.get("Content-Type") == "image/png", shown and shown.headers.get("Content-Type"))
    shown = requests.get(detail["view_url"], headers={"Origin": out["WebUrl"]}, timeout=30)
    check("the bucket answers CORS for the app",
          shown.headers.get("Access-Control-Allow-Origin") in ("*", out["WebUrl"]), dict(shown.headers))
check("another user can't read them",
      pat("GET", f"/assistant/files/{note['file_id'] if note else 'x'}").status_code == 404)

print("\nThe sandbox's files (Pro)")
csv = upload(pat, "smoke-sales.csv", b"item,amount\ntea,12\ncoffee,30\ncake,8\n", "text/csv")
if csv:
    thread = new_thread()
    events = chat(pat, thread, "Use open_file to put the attached CSV in the sandbox, then use bash to run Python "
                               "that adds up its amount column. Reply with the total only.",
                  attachments=[csv["file_id"]])
    answered("asked to work on an attached file in the sandbox", events)
    check("open_file put it in the sandbox", any("files/smoke-sales.csv" in r for r in results_of(events, "open_file")),
          results_of(events, "open_file"))
    check("the total is right", "50" in text_of(events), text_of(events))
    events = chat(pat, thread, "In the sandbox, use Python with matplotlib to draw a bar chart of that CSV's amounts, "
                               "save it as chart.png, then save it for me with save_file.")
    answered("asked for a chart", events)
    charts = [f for f in pat("GET", "/assistant/files", params={"kind": "image"}).json()["items"]
              if f["source"] == "assistant"]
    check("save_file saved the chart from the sandbox", charts and charts[0]["content_type"] == "image/png",
          (tools_of(events), results_of(events, "save_file")))

print()
if failures:
    print(f"{len(failures)} failed: " + "; ".join(failures))
    sys.exit(1)
print("All assistant checks passed.")
