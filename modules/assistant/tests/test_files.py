"""
The users' files: what they attach to messages (uploaded straight to S3 with
a presigned POST, then sent with a message), what the model is sent of them
(AttachmentsMiddleware), and what agents make (write_file, save_file, with
versions) or open in the sandbox (open_file); the HTTP API that lists,
links and deletes them (/assistant/files).
"""
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage

from assistant.context import AgentContext
from assistant.factory import thread_config
from assistant.handler import handler
from shared.auth import user_from_claims
from testkit.http import call, claims

THREAD = "11111111-2222-3333-4444-555555555555"   # conftest's SESSION
FILES_BUCKET = "assistant-files-test"            # conftest's

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def upload(ctx, user, name: str, data: bytes, content_type: str | None = None, *, finish: bool = True) -> dict:
    """What the page does to attach a file: asks for an upload, then sends it to S3 (here: put as the form would)."""
    body = {"name": name, "size": len(data), **({"content_type": content_type} if content_type else {})}
    status, ticket = call(handler, "POST", "/assistant/files", user, body=body)
    assert status == 201, ticket
    assert ticket["fields"]["Content-Type"] == ticket["file"]["content_type"]
    assert "pending" in ticket["fields"]["tagging"]
    if finish:
        ctx.s3.put_object(Bucket=FILES_BUCKET, Key=ticket["fields"]["key"], Body=data,
                          ContentType=ticket["file"]["content_type"], Tagging="pending=true")
    return ticket["file"]


def opened(user) -> dict:
    status, conversation = call(handler, "GET", f"/assistant/conversations/{THREAD}", user)
    assert status == 200, conversation
    return conversation


def question_sent(ctx, call_index: int = 0) -> HumanMessage:
    """The user's message as the model was sent it (the last one), files and all."""
    return [m for m in ctx.model_calls()[call_index] if isinstance(m, HumanMessage)][-1]


def files_of(user, **query) -> list[dict]:
    status, page = call(handler, "GET", "/assistant/files", user, query={k: str(v) for k, v in query.items()})
    assert status == 200, page
    return page["items"]


def pro():
    return claims("pat", "t1", "member", plan="pro")


# ---------- attaching files ----------

def test_a_file_is_uploaded_then_sent_with_a_message(ctx, alice):
    file = upload(ctx, alice, "notes.txt", b"Buy more green tea.", "text/plain")
    assert file["status"] == "pending" and files_of(alice) == []   # not one of their files until it's sent
    ctx.script(ctx.answer("You need green tea."))

    ctx.run(ctx.post("What do I need?", alice, message_id="m1", attachments=[file["file_id"]]), alice)

    [question, _] = opened(alice)["messages"]
    assert question["content"] == [
        {"type": "text", "text": "What do I need?"},
        {"type": "document", "source": {"type": "file", "value": file["file_id"], "mimeType": "text/plain"},
         "metadata": {"name": "notes.txt", "size": 19, "kind": "text"}}]
    sent = question_sent(ctx).content
    assert sent[0]["text"].startswith("What do I need?\n\n[Attached file: notes.txt (text/plain, 1 KB), id ")
    assert sent[1] == {"type": "text",
                       "text": f'<file name="notes.txt" id="{file["file_id"]}">\nBuy more green tea.\n</file>'}
    [kept] = files_of(alice)
    assert (kept["status"], kept["thread_id"], kept["source"]) == ("ready", THREAD, "upload")
    key = f"t1/alice/{file['file_id']}/1"
    assert ctx.s3.get_object_tagging(Bucket=FILES_BUCKET, Key=key)["TagSet"] == []   # the lifecycle rule leaves it


def test_an_image_is_sent_to_the_model_as_an_image_and_kept_as_a_line(ctx, alice):
    image = upload(ctx, alice, "receipt.png", PNG, "image/png")
    ctx.script(ctx.answer("A receipt."))

    ctx.run(ctx.post("", alice, message_id="m1", attachments=[image["file_id"]]), alice)   # a file alone

    [text, picture] = question_sent(ctx).content
    assert picture["type"] == "image" and picture["mime_type"] == "image/png" and picture["base64"]
    assert text["text"].startswith("[Attached file: receipt.png (image/png")
    # The agent's state names the file; its bytes never go into a checkpoint.
    config = thread_config(AgentContext(user=user_from_claims(alice), authorization="", session_id=THREAD),
                           "assistant")
    saved = ctx.state.checkpointer.get_tuple(config).checkpoint["channel_values"]["messages"][0]
    assert isinstance(saved.content, str) and saved.additional_kwargs["attachments"][0]["file_id"] == image["file_id"]
    assert ctx.title_model.calls[-1][1].content == "receipt.png"   # what it's titled from


def test_a_file_the_model_cant_read_is_named_and_opened_in_the_sandbox(ctx):
    user = pro()   # the sandbox is in the plan
    report = upload(ctx, user, "report.pdf", b"%PDF-1.7 ...", "application/pdf")
    ctx.script(ctx.tool_call("open_file", {"file_id": report["file_id"]}), ctx.answer("Opened it."))

    events = ctx.run(ctx.post("Summarize this", user, attachments=[report["file_id"]]), user)

    assert len(question_sent(ctx).content) == 1   # the line only
    result = next(e["content"] for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result == "report.pdf is at files/report.pdf (in a new sandbox: files from before are gone)."
    conversation_id = AgentContext(user=user_from_claims(user), authorization="", session_id=THREAD).conversation_id
    assert ctx.sandbox.files[conversation_id, "files/report.pdf"] == b"%PDF-1.7 ..."


def test_a_message_takes_only_the_users_own_files_once_uploaded(ctx, alice):
    pending = upload(ctx, alice, "notes.txt", b"x", finish=False)
    status, body = ctx.send("Hi", alice, attachments=[pending["file_id"]])
    assert status == 400 and "hasn't finished uploading" in body["message"]

    bobs = upload(ctx, claims("bob", "t1", "member"), "bob.txt", b"x")
    assert ctx.send("Hi", alice, attachments=[bobs["file_id"]])[0] == 404
    assert ctx.send("Hi", alice, attachments=["no-such-file"])[0] == 404
    assert ctx.send("Hi", alice, attachments=["a", "b", "c", "d", "e", "f"])[0] == 422
    assert ctx.send("   ", alice)[0] == 422
    assert ctx.conversations.get("t1", "alice", THREAD) is None   # nothing was saved, nothing counted
    assert ctx.usage.usage.get("t1", datetime.now(UTC).strftime("%Y-%m")).messages == 0


def test_uploads_are_checked_before_anything_is_stored(ctx, alice):
    for body, message in [({"name": "setup.exe", "size": 10, "content_type": "application/x-msdownload"},
                           "can't be attached"),
                          ({"name": "big.png", "size": 6 * 1024 * 1024, "content_type": "image/png"}, "up to 5 MB"),
                          ({"name": "big.zip", "size": 21 * 1024 * 1024}, "up to 20 MB")]:
        status, answer = call(handler, "POST", "/assistant/files", alice, body=body)
        assert status == 400 and message in answer["message"], body
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    ctx.table.put_item(Item={"pk": "TENANT#t1", "sk": f"USER#alice#UPLOADS#{hour}", "messages": 20})
    status, answer = call(handler, "POST", "/assistant/files", alice, body={"name": "a.txt", "size": 1})
    assert status == 429 and answer["message"].startswith("You can attach 20 files an hour")


def test_an_upload_never_sent_is_gone_after_a_day(ctx, alice):
    file = upload(ctx, alice, "notes.txt", b"x")
    ctx.table.update_item(Key={"pk": "TENANT#t1#USER#alice#FILES", "sk": f"FILE#{file['file_id']}"},
                          UpdateExpression="SET expires_at = :past",
                          ExpressionAttributeValues={":past": int(time.time()) - 1})

    assert ctx.send("Hi", alice, attachments=[file["file_id"]])[0] == 404
    assert call(handler, "GET", f"/assistant/files/{file['file_id']}", alice)[0] == 404


def test_answering_again_keeps_the_files(ctx, alice):
    file = upload(ctx, alice, "notes.txt", b"Buy more green tea.", "text/plain")
    ctx.script(ctx.answer("Tea."))
    ctx.run(ctx.post("What do I need?", alice, message_id="m1", attachments=[file["file_id"]]), alice)
    ctx.script(ctx.answer("Green tea."))

    ctx.run(ctx.post("What do I need?", alice, message_id="m1"), alice)   # Regenerate: the page sends no files

    assert "Buy more green tea." in question_sent(ctx).content[1]["text"]


# ---------- files the assistant makes ----------

def test_the_assistant_writes_a_file_and_saves_it_again_as_a_new_version(ctx, alice):
    ctx.script(ctx.tool_call("write_file", {"name": "report.md", "content": "# Orders\nNone yet."}),
               ctx.answer("Here is your report."))
    events = ctx.chat("Write me a report", alice)

    saved = json.loads(next(e["content"] for e in events if e["type"] == "TOOL_CALL_RESULT"))
    assert saved == {"file_id": saved["file_id"], "name": "report.md", "content_type": "text/markdown",
                     "size": 18, "version": 1}
    [file] = files_of(alice, source="assistant")
    assert (file["agent"], file["thread_id"], file["kind"]) == ("assistant", THREAD, "text")

    ctx.script(ctx.tool_call("write_file", {"name": "Report.md", "content": "# Orders\nOne: green tea."}),
               ctx.answer("Updated."))
    ctx.chat("Add the tea order", alice)

    [file] = files_of(alice, source="assistant")
    assert [v["version"] for v in file["versions"]] == [1, 2] and file["size"] == 24
    status, detail = call(handler, "GET", f"/assistant/files/{file['file_id']}", alice)
    assert status == 200 and detail["view_url"] is None and "report.md" in detail["download_url"]
    assert ctx.s3.get_object(Bucket=FILES_BUCKET, Key=f"t1/alice/{file['file_id']}/2")["Body"].read() \
        == b"# Orders\nOne: green tea."
    first = call(handler, "GET", f"/assistant/files/{file['file_id']}", alice, query={"version": "1"})[1]
    assert f"/{file['file_id']}/1?" in first["download_url"]
    assert call(handler, "GET", f"/assistant/files/{file['file_id']}", alice, query={"version": "3"})[0] == 404


def test_the_assistant_saves_a_chart_from_the_sandbox(ctx):
    user = pro()
    conversation_id = AgentContext(user=user_from_claims(user), authorization="", session_id=THREAD).conversation_id
    ctx.sandbox.started.append(conversation_id)
    ctx.sandbox.files[conversation_id, "out/chart.png"] = PNG
    ctx.script(ctx.tool_call("save_file", {"path": "out/chart.png"}), ctx.answer("Here is the chart."))

    ctx.chat("Chart my orders", user)

    [chart] = files_of(user, kind="image")
    assert chart["name"] == "chart.png" and chart["content_type"] == "image/png" and chart["source"] == "assistant"
    assert call(handler, "GET", f"/assistant/files/{chart['file_id']}", user)[1]["view_url"]


def test_a_file_from_a_sandbox_that_ended_is_not_saved(ctx):
    user = pro()
    ctx.script(ctx.tool_call("save_file", {"path": "chart.png"}), ctx.answer("It's gone."))

    events = ctx.chat("Save the chart", user)

    result = next(e["content"] for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result.startswith("Error: the sandbox has ended")
    assert files_of(user) == []


def test_without_the_sandbox_in_the_plan_the_assistant_still_writes_files(ctx, alice):
    ctx.script(ctx.answer("Hi."))
    ctx.chat("Hi", alice)
    shown = set(ctx.tools_shown()[0])
    assert "write_file" in shown and not {"save_file", "open_file", "bash"} & shown


# ---------- the user's files ----------

def test_files_are_listed_newest_first_filtered_and_deleted(ctx, alice):
    ids = [upload(ctx, alice, name, data)["file_id"]
           for name, data in [("a.txt", b"a"), ("b.png", PNG), ("c.csv", b"x,y")]]
    ctx.script(ctx.answer("OK."))
    ctx.run(ctx.post("Here", alice, attachments=ids), alice)

    assert [f["name"] for f in files_of(alice)] == ["c.csv", "b.png", "a.txt"]
    assert [f["name"] for f in files_of(alice, kind="data")] == ["c.csv"]
    status, page = call(handler, "GET", "/assistant/files", alice, query={"limit": "2"})
    rest = call(handler, "GET", "/assistant/files", alice, query={"limit": "2", "cursor": page["next_cursor"]})[1]
    assert [f["name"] for f in page["items"] + rest["items"]] == ["c.csv", "b.png", "a.txt"]
    assert files_of(claims("bob", "t1", "member")) == []

    assert call(handler, "DELETE", f"/assistant/files/{ids[1]}", claims("bob", "t1", "member"))[0] == 404
    assert call(handler, "DELETE", f"/assistant/files/{ids[1]}", alice)[0] == 204
    assert [f["name"] for f in files_of(alice)] == ["c.csv", "a.txt"]
    assert ctx.s3.list_objects_v2(Bucket=FILES_BUCKET, Prefix=f"t1/alice/{ids[1]}/").get("KeyCount") == 0
    assert call(handler, "GET", f"/assistant/files/{ids[1]}", alice)[0] == 404
    # The message still names it; the model, then, gets its line only.
    ctx.script(ctx.answer("Gone."))
    ctx.run(ctx.post("Here", alice, message_id=opened(alice)["messages"][0]["id"]), alice)
    assert len(question_sent(ctx).content) == 3   # the line, and the two files still there


# ---------- what AWS must allow ----------

S3_ACTIONS = {"HeadObject": "s3:GetObject", "GetObject": "s3:GetObject", "PutObject": "s3:PutObject",
              "DeleteObjects": "s3:DeleteObject", "DeleteObjectTagging": "s3:DeleteObjectTagging",
              "ListObjectsV2": "s3:ListBucket"}


def allowed(resource: str, *, role: str | None = None, function: str | None = None) -> set[str]:
    """The actions template.yaml lets AssistantRuntimeRole (`role`) or a function's policies do on `resource`."""
    class Template(yaml.SafeLoader):
        pass

    Template.add_multi_constructor("!", lambda loader, tag, node:
                                   f"!{tag} {node.value}" if isinstance(node, yaml.ScalarNode) else None)
    template = yaml.load((Path(__file__).resolve().parents[3] / "template.yaml").read_text(), Template)
    properties = template["Resources"][role or function]["Properties"]
    statements = [s for policy in properties["Policies"] if isinstance(policy, dict)
                  for s in (policy.get("PolicyDocument") or policy).get("Statement", [])]
    return {action for s in statements if resource in (s["Resource"] if isinstance(s["Resource"], list)
                                                       else [s["Resource"]]) for action in s["Action"]}


def test_the_roles_allow_every_call_made_to_the_files_bucket(ctx, alice):
    calls: dict[str, set[str]] = {"api": set(), "runtime": set()}
    doing = {"who": "api"}
    ctx.s3.meta.events.register("before-call.s3", lambda model, **_: calls[doing["who"]].add(S3_ACTIONS[model.name]))

    image = upload(ctx, alice, "receipt.png", PNG, "image/png")
    run_id = ctx.post("What is this?", alice, attachments=[image["file_id"]])
    ctx.script(ctx.tool_call("write_file", {"name": "notes.md", "content": "A receipt."}), ctx.answer("Saved."))
    doing["who"] = "runtime"
    ctx.run(run_id, alice)
    doing["who"] = "api"
    call(handler, "GET", f"/assistant/files/{image['file_id']}", alice)
    call(handler, "DELETE", f"/assistant/files/{image['file_id']}", alice)

    objects = "!Sub ${AssistantFilesBucket.Arn}/*"
    # The browser's upload acts as the HTTP API's role, which signed it: the object and its tag.
    assert calls["api"] | {"s3:PutObject", "s3:PutObjectTagging"} \
        <= allowed(objects, function="AssistantApiFunction")
    assert calls["runtime"] == {"s3:GetObject", "s3:PutObject"}
    assert calls["runtime"] <= allowed(objects, role="AssistantRuntimeRole")
    assert {"s3:DeleteObject"} <= allowed(objects, function="AssistantPurgeFunction")
    assert {"s3:ListBucket"} <= allowed("!GetAtt AssistantFilesBucket.Arn", function="AssistantPurgeFunction")
