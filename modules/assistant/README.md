# Assistant module (AI agents on Amazon Bedrock AgentCore)

LangChain agents (`create_agent`) that answer signed-in users about their
workspace, **each defined in a Markdown file** (Claude Code's subagent
format). They run on **AgentCore Runtime** (one microVM per conversation)
instead of Lambda, keep each agent's state in each conversation as LangGraph
checkpoints in **DynamoDB** (`DynamoDBSaver`), and use the same Cognito ID
token, plan catalog and module rules as the rest of the backend.

```
browser ──ID token──▶ HTTP API POST /assistant/conversations/{thread}/messages ──▶ handler.py (Lambda)
                        ConversationService.send: the agent exists (agents/*.md), the plan allows it
                        (UsageService.start_message → 402), no answer is coming (409); saves the
                        message and the run that will answer it ──▶ {run_id}

browser ──ID token──▶ AgentCore Runtime ── JWT authorizer (user pool, aud, token_use=id)
                        │  POST /invocations: AG-UI RunAgentInput (threadId, runId) in, AG-UI events (SSE) back
                        ▼
                 app.py  invoke()
                   1. claims → CurrentUser (shared.auth rules)
                   2. the run: in this microVM's runs.py (follow it from the start), or the
                      conversation's run still to answer (start it in a worker thread, follow it)
                 app.py  handle(), the run, whatever happens to the requests reading it
                   3. agent.stream(...)   built by factory.py from its Markdown file
                        ├─ UsageMiddleware         tokens of every model call → DynamoDB, stop at the cap
                        ├─ ConversationMiddleware  repair cut-off tool calls; send recent history only
                        ├─ PlanToolsMiddleware     hide tools the plan doesn't include
                        ├─ Model/ToolCallLimit, ToolErrorMiddleware (built-in)
                        ├─ checkpointer      DynamoDBSaver (each agent's side of the conversation;
                        │                                   over 350 KB to S3)
                        ├─ tools ──user's own token──▶ HTTP API (/orders ...)
                        ├─ bash ──InvokeCodeInterpreter──▶ sandbox microVM (one per conversation,
                        │                                   no AWS role, no internet by default)
                        └─ task ──▶ a subagent (fresh context, same user and sandbox, no checkpoints)
                      stops early when the user pressed Stop (asked once a second)
                      meanwhile, for a new conversation: titles.py, one small call for its title
                   4. ConversationLog.record     the history gets what streamed (AG-UI messages),
                                                 then the conversation's run is removed

browser ──ID token──▶ HTTP API /assistant/conversations ──▶ handler.py (Lambda): list, open, rename,
                                                              delete, stop an answer, rate one
                                                              └─ delete ──SQS──▶ purge.py: the checkpoints
DynamoDB TTL removes an idle conversation's META ──stream──▶ purge.py: its messages and checkpoints
```

## Files

```
src/assistant/
├── agents/              one Markdown file per agent: the part you edit most
│   ├── assistant.md     the main agent (the frontend's default)
│   └── analyst.md       a subagent the assistant delegates analysis to
├── definitions.py       reads and checks agents/*.md (at startup)
├── llm.py               the models: OpenRouter (vendor/model ids) or Bedrock, and the API key
├── factory.py           definitions → running agents: model, tools, middleware, subagents
├── delegation.py        the task tool: an agent runs one of its subagents
├── tools/               the tools agents can name in `tools:` (a registry in __init__.py)
│   ├── orders.py        list_orders, get_order (the HTTP API, with the user's token)
│   ├── usage.py         assistant_usage
│   └── bash.py          bash (the sandbox)
├── middleware/          one file per middleware (usage, conversation, plan_tools, errors)
├── titles.py            names a new conversation from its first message, while the agent answers
├── app.py               AgentCore entry point (the SDK's AGUIApp): auth, the run, stop, history, stream
├── stop.py              Stop ends the run's model call, and estimates what the call cost
├── runs.py              this microVM's runs: each runs in a worker thread into a log any request reads
├── agui.py              the agent's stream → AG-UI events; Transcript: events → AG-UI messages
├── context.py           AgentContext: who the agents work for (runtime context, never in a prompt)
├── schemas.py           what the HTTP API and the AG-UI endpoint accept and return (no AG-UI import)
├── checkpoints.py       deletes a thread's checkpoints, as DynamoDBSaver laid them out (boto3 only)
├── sandbox.py           AgentCore Code Interpreter adapter: one sandbox per conversation
├── service.py, repository.py, models.py   usage per workspace; conversations and their history
├── api.py, handler.py   Lambda: the HTTP API for the users' conversations (sending a message too)
├── agent_names.py       the agents' names without loading them, for that Lambda
├── purge.py             Lambda: what is left of a deleted conversation or a removed member's (SQS), or an
│                        expired one (stream)
└── container.py         composition root (models from llm.py, the checkpointer, DynamoDB, SQS, httpx)
../main.py               what AgentCore starts (root of the zip)
```

Each layer knows only the one below it: agents (Markdown) name tools and
subagents; the factory wires them with middleware; tools and middleware use
the service and the adapters; only `container.py` creates real AWS clients.

The two Lambdas run this same package with only the shared layer, so
`api.py`, `handler.py`, `purge.py` and everything they import never import
LangChain, the AgentCore SDK, AG-UI or httpx (`container.get_agents()`
imports those when it runs). `test_conversations.py` checks it.

## Agents in Markdown

An agent is a file in `src/assistant/agents/` (subfolders work too), in the
format of Claude Code's subagents: YAML frontmatter, then the system prompt.

```markdown
---
name: analyst
description: Data analyst for the workspace's orders. Use for trends, comparisons and reports.
tools: list_orders, get_order, bash
model: inherit
maxTurns: 12
---
You are a data analyst working for another agent...
```

| Field | Meaning |
|---|---|
| `name` (required) | Lowercase letters, digits, `-`. The frontend picks an agent by it. |
| `description` (required) | When to use it: the calling agent reads this to decide when to delegate. |
| `tools` | Names from `tools/__init__.py`. Omitted = every tool, as in Claude Code: list them. |
| `disallowedTools` | Removed from `tools`. |
| `model` | `inherit` (the stack's `MODEL_ID`), `sonnet`, `opus`, `haiku` (Claude through OpenRouter), an OpenRouter model (`vendor/model`) or a Bedrock model id. |
| `maxTurns` | Model calls per run, 1 to 50 (default 15). |
| `subagents` | **This app's addition:** the agents this one may delegate to, through the `task` tool. |

Other Claude Code fields (`color`, `memory`, `skills`, `hooks`, ...) are
ignored with a warning in the logs. Every file is checked when the agents
load: a missing field, an unknown tool or subagent, or a bad model fails
`main.py` at startup and `uv run pytest` (test_definitions.py), not a
conversation.

**Subagents**, as in Claude Code: the calling agent writes the task; the
subagent starts with only its own system prompt and that task, works for the
same user (same token, same plan, same sandbox and files), and its final
answer comes back as the `task` tool's result. The user sees only the calling
agent's words (a `TOOL_CALL_START` named `task` marks the hand-off). Two
differences, both to bound a message's cost: an agent may only call the
subagents it lists, and a subagent can't delegate further (one level). A
subagent keeps no checkpoints, so delegating adds model tokens (counted
against the same allowance) but no checkpoint writes.

**Talking to an agent directly:** the request's `"agent"` field picks any
agent (default `assistant`). Each agent keeps its own side of a conversation
(its own checkpoint thread); they share the conversation's sandbox.

**Adding an agent:** write `agents/<name>.md`, add it to another agent's
`subagents:` if it should be delegated to, run `uv run pytest modules/assistant`,
and deploy. No Python needed unless it needs a new tool: then add the tool in
`tools/` and to `make_tools`.

**When not to add one:** a subagent costs a fresh model context each time.
For a few extra instructions, a longer system prompt (or a skill-like section
in it) is cheaper. Subagents pay off for focused, multi-step work whose
details the main conversation doesn't need: analysis, reports, research.

## How a message is handled

**Before the agent runs** (cheap, no model call):

1. When the message is sent (the HTTP API, `ConversationService.send`),
   `UsageService.start_message()` counts it in conditional DynamoDB updates,
   like orders' plan limit (rule 8). First the user's hour: 429, with when to
   try again, once they have sent the plan's messages this hour (UTC), so one
   person can't use up the workspace's month in an afternoon; a fixed window,
   so at most twice the limit fits in an hour that straddles two. Then the
   month: 402 when the workspace is out of messages **or** tokens. The plan
   comes from the token. Limits live in `shared/plans.py`
   (`assistant_messages_per_user_hour`: Free 20, Pro 60, Business 120;
   `assistant_messages_per_month`, `assistant_tokens_per_month`).
2. When AgentCore gets the run (`app.invoke()`), the token's claims and the
   run's `forwardedProps.tenantId` (the workspace the page works in) become a
   `CurrentUser` with `shared.auth.user_from_claims`, the same rules as every
   HTTP route's `X-Tenant-Id` (ID tokens only, a workspace the token lists).
   AgentCore verified the signature, issuer, audience and `token_use`
   already, and forwards the `Authorization` header
   (`RequestHeaderAllowlist`; it passes on no custom headers, hence the
   body). The run is looked up under that user's own keys, and the tools call
   the HTTP API with the same token and workspace.

**While it runs**, in middleware (`middleware/`, in the order `factory.py` sets):

- `UsageMiddleware.wrap_model_call` adds each model call's `usage_metadata` to
  the month's tokens, read from the model's response itself (it is the outermost
  wrapper; `after_model` would see whatever other middleware appended first).
  A call the user stopped has no `usage_metadata`, so it adds `StopModel`'s
  estimate instead (see Stop below).
  When the cap is reached, `before_model` ends the run with a short message
  (`jump_to: "end"`) *before* the next model call, so the history stays valid
  for the next turn (every tool call already has its result).
- `ConversationMiddleware` keeps the conversation usable. A message cut off
  while tools ran (client gone, microVM stopped, a crash) leaves tool calls
  without results in the checkpoint, and the model API rejects such a history on
  every later message; `before_agent` gives them an "interrupted" result. Each
  model call is sent only the recent history (`MAX_HISTORY_TOKENS`, cut at a
  user message), so a long conversation costs the same per call; the
  checkpoint keeps everything.
- Built-in `ModelCallLimitMiddleware` (the agent's `maxTurns`, default 15) and
  `ToolCallLimitMiddleware` (15) bound one run, whatever the model decides; a
  subagent's run has its own. `ToolErrorMiddleware` turns a
  tool's exception into a result the model can explain (details only in the
  logs) instead of failing the message.

The conversation itself is checkpointed by `DynamoDBSaver`
(langgraph-checkpoint-aws) in its own table, `AssistantCheckpointTable`: the
library names the keys (`PK`, `SK`) and the TTL attribute (`ttl`). The run
checkpoints once, when it ends (`durability="exit"` in `app.handle`), not
after every step: a run that dies with its microVM is lost to the agent, but
the history has what streamed. Each checkpoint holds the agent's whole state,
gzip-compressed; one over 350 KB goes to `AssistantCheckpointBucket`. Each
expires `CHECKPOINT_DAYS` (30, `settings.py`) after it was written (the
library sets the bucket's lifecycle rule to match), and only the newest one
matters, so an active conversation never loses its state. An agent whose
checkpoints expired while the conversation is kept starts again from the
history (`app.restored`): its own side of it (each message is saved with the
agent it was said to or by), turned back into its messages
(`agui.agent_messages`). So it remembers what the user sees; only a tool call
cut off mid-stream (Stop) is left out. When the conversation is deleted or
expires, or its user leaves the workspace, the purge worker deletes them all. The thread id is a hash of user,
session and agent (`thread_config()`): the client picks the session id, so
someone sending another user's session id still gets their own conversation,
and the table, which knows only threads, needs nothing else.

There is no long-term memory across conversations: the agent knows what it
was told in this conversation, and what the tools return.

**Tools** call the existing HTTP API with the caller's own token. The API
checks the tenant and the role as it does for the frontend, no module imports
another (rule 1), and the agent can never do more than the user could. The
token reaches tools through `runtime.context`; the model never sees it.

## The terminal (bash tool)

The `bash` tool gives the model a Linux shell (bash, Python, Node.js) in an
**AgentCore Code Interpreter** sandbox (`AssistantSandbox` in `template.yaml`):

- **Never next to the agent.** The model's commands run in a separate microVM,
  not in the agent's, which holds the execution role and the user's token. The
  sandbox has no AWS role, and with `AssistantSandboxNetwork=SANDBOX` (the
  default) no internet. `PUBLIC` allows `pip install` and `curl`, so commands
  could also send data out: assume a prompt injection will try.
- **One sandbox per conversation**, keyed by `conversation_id` (user + session),
  like the checkpoints. Files persist between commands for
  `SANDBOX_SESSION_SECONDS` (15 minutes) after it started; then the next
  command gets a new, empty sandbox and the result tells the model so. Each
  call is a new shell: the tool tells the model to chain steps with `&&`.
- **Only what you give it.** It can't reach the app's API or data. To work on
  workspace data, the model fetches it with the other tools and writes a file.
- **Plan rule**: `assistant_sandbox_sessions_per_month` in `shared/plans.py`
  (Free 0 = not included, Pro 200, Business 2,000), counted with a conditional
  update like messages. `PlanToolsMiddleware` hides the tool from plans
  without it, and the tool refuses anyway (a model can call a tool it wasn't
  shown). Output is cut to 10,000 characters per stream, keeping the start and
  the end.
- **Cost**: the sandbox is billed like Runtime ($0.0895 per vCPU-hour, $0.00945
  per GB-hour in Singapore) for the minutes it is alive, at most 15 per
  session. AWS doesn't publish its memory size; check the first bill.

## Calling it from the frontend

A message is sent in two steps, as in ChatGPT: the HTTP API saves it, with
the run that will answer it, then AgentCore runs it and streams the answer.
The endpoint speaks [AG-UI](https://docs.ag-ui.com) (the runtime's protocol is
`AGUI`), so any AG-UI client works, such as `@ag-ui/client` or CopilotKit. It
is the only format: anything new the frontend needs is an AG-UI event
(`CUSTOM` where AG-UI has none), not a format of our own.

```ts
import { HttpAgent } from "@ag-ui/client";

const threadId = crypto.randomUUID();                    // one per conversation
// 1. Saved before anything runs (402 at the plan's limit, 409 while an answer is coming).
const { run_id } = await api(`/assistant/conversations/${threadId}/messages`, {
  method: "POST", body: { id: crypto.randomUUID(), content: "What did we order last week?", agent: "assistant" },
});
// 2. Its answer, streamed. The same call again follows the same run, from the start (a reload).
const agent = new HttpAgent({
  url: assistantUrl,                                     // the AssistantUrl output
  threadId,
  headers: { Authorization: `Bearer ${idToken}`,         // the same token the HTTP API takes
             "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": threadId },
});
await agent.runAgent({ runId: run_id }, {
  onTextMessageContentEvent: ({ event }) => append(event.delta),
  onToolCallStartEvent: ({ event }) => showWorking(event.toolCallName),   // "task": a subagent
  onRunErrorEvent: ({ event }) => showError(event.code, event.message),
});
```

- **The threadId is the conversation**, and the AgentCore session: send it as
  the session header too (33+ characters; a UUID works), or the run fails with
  `VALIDATION_ERROR`. A new threadId starts a new conversation.
- **The runId is the run the HTTP API gave the message.** The runtime answers
  the message saved for it, and reads nothing else from the request: the
  client's `messages`, `state`, `tools` and `context` are ignored, so a client
  can't make up what the assistant said. Frontend tools aren't supported.
- **Events**: `RUN_STARTED`; the answer as `TEXT_MESSAGE_START`, `_CONTENT`,
  `_END` (one message per model call); each tool call as `TOOL_CALL_START`,
  `_ARGS`, `_END`, then `TOOL_CALL_RESULT` with the tool's output; then
  `RUN_FINISHED` with `result: {"agent": ...}` (and `"stopped": true` after a
  Stop) and `usage` (this agent's tokens; a subagent's count against the
  allowance but aren't in it).
- **Errors are `RUN_ERROR` events** with a `code`: `UNAUTHORIZED` or
  `ACCESS_DENIED` (sign in again), `VALIDATION_ERROR`, `NOT_FOUND` (no such
  run: it ended and the history has it, or it was never sent),
  `INTERNAL_ERROR`. In AGUI mode AgentCore's own errors (bad token,
  throttling, a busy session) are `RUN_ERROR` events too, with their HTTP
  status. The plan's limit is the HTTP API's 402, before anything streams.
- **WebSocket**: `/ws` streams the same events (the SDK's `AGUIApp`); not
  tried on AgentCore yet.

**From a browser**: the AgentCore endpoint answers CORS itself, for any
origin (checked on the dev stack, October 2026: the preflight allows
`authorization`, `content-type` and the session header, and responses carry
`Access-Control-Allow-Origin: *`), so the app calls it directly. The web app's
Assistant page (`web/src/features/assistant/`) is a working client. For a
custom domain, AWS's pattern is CloudFront in front of the runtime (see
Sources).

## Conversations (send, edit, follow, stop, rate, list, reopen, rename, delete)

As in ChatGPT and Claude, the app keeps its own record of each conversation,
every message as the user saw it, tool calls and results included, separate
from what the model is sent (the agent's checkpoints), and the server, not
the page, knows what is running. Routes on the HTTP API, with the same ID
token (`handler.py`, a Lambda):

| Route | |
|---|---|
| `POST /assistant/conversations/{threadId}/messages` `{"id", "content", "agent"?, "replaces"?}` | send, or edit (`replaces`): 201 `{"run_id"}`, 402 at the plan's limit, 429 at the user's hourly limit, 409 while an answer is coming |
| `POST /assistant/conversations/{threadId}/runs/{runId}/cancel` | stop the answer (204, even after it ended) |
| `PATCH /assistant/conversations/{threadId}/messages/{messageId}` `{"rating": "up" \| "down" \| null}` | rate an answer, or take the rating back (204) |
| `GET /assistant/conversations?limit=20&cursor=` | the caller's conversations, most recently used first |
| `GET /assistant/conversations/{threadId}` | title, `run_id` while an answer is coming, the latest 200 messages as AG-UI messages, and `ratings` |
| `PATCH /assistant/conversations/{threadId}` `{"title"}` | rename (cut to 80 characters) |
| `DELETE /assistant/conversations/{threadId}` | delete (204) |

- **Sending** (`ConversationService.send`) counts the message against the
  plan, then saves it, creating the conversation the first time (titled with
  it), with its run on `META`: `run = {run_id, message_id, prompt, agent,
  since}`. From then on the conversation has it, whatever the page does: a
  reload a moment later finds the message and the run. One answer at a time
  per conversation (409).
- **Editing and answering again**, as in ChatGPT and the Vercel chatbot (no
  branches kept): `replaces` names an earlier message of the user's, and the
  new one takes its place, with everything after it. The same message id sent
  again (Try again, Regenerate) replaces itself, so the old answer goes. The
  history is cut in one conditional update of `META` (`message_count`, which
  must be what the service read, and no answer coming), then the message is
  written and what came after it deleted; reads stop at `message_count`, so
  a delete that fails half way leaves nothing anyone sees. The agent goes
  back too (`Run.rewind_to`, `app.rewound`): its next run starts from its
  saved state with that user message and everything after it removed. The
  user's messages are checkpointed with their AG-UI id, which is how it
  finds them. Only the agent the message went to goes back; other agents
  that spoke in the conversation keep their own side of it.
- **The run** happens in the conversation's AgentCore microVM (`app.py`,
  `runs.py`). The first request for it starts it, reading the prompt from
  `META.run`; it goes on in a worker thread whatever happens to that request,
  writing its AG-UI events to a log in memory. Every request for the run, the
  first or one after a reload, reads that log from the start, then follows it
  live. The events keep their message ids, so a page that follows it again
  draws the same messages. AgentCore sends every request of a session to its
  microVM, so the run and its readers are in one process. If the microVM
  goes, its runs go with it: `META.run` says one is still due, and the next
  request for it starts it again. `/ping` answers `HealthyBusy` while a run
  goes, so a session whose browser left isn't stopped as idle. A finished run's
  log stays 10 minutes; after that a request for it gets `NOT_FOUND`, and the
  page reads the history, which has the answer.
- **Written as it ends** (`service.ConversationLog`): when the run ends,
  however it ended, the conversation gets what streamed, then loses its run,
  in that order, so a page that finds it answered finds the whole answer.
  `agui.Transcript` assembles the AG-UI events into messages as
  `@ag-ui/client` does, so a reopened conversation and the live one are the
  same objects, with the same ids. A tool's output is kept up to 10,000
  characters. A run started more than 60 minutes ago, the longest AgentCore
  streams, counts for nothing: its runtime stopped before it could say so.
- **Stop** marks the run (`run.cancelled`); the run asks at most once a
  second, between chunks, then ends with `RUN_FINISHED` `{"stopped": true}`
  and keeps what it wrote. Deleting the conversation stops it the same way. A
  long tool call (a sandbox command, a subagent) finishes first. The model's
  call ends too (`StopModel` raises from its next token): closing the stream
  waits for the step that is running, so a long answer would otherwise go on
  being written, and billed, to its end, with the conversation busy. A
  stopped call never gets the model's token counts (they come with its last
  chunk), so `StopModel` estimates them, about 4 characters a token, from
  what the call was sent and what it wrote. They count against the plan and
  appear in the `Assistant answered` log line (`stop.py`). The agent
  keeps what the user saw, too (`keep_what_the_user_saw`): closing the stream
  mid-step leaves a tool's result that just came back as a pending write,
  which the next message's run drops, and the answer's text so far nowhere, so
  both are added to its checkpoint.
- **Reloading**: the page reads the conversation; with a `run_id`, it sends
  AgentCore that run and draws it from the start, then live.
- **A stream that breaks** (the network, AgentCore failing a request) is
  read on from where it was, as OpenAI's background responses
  (`starting_after`) and LangGraph streams (`Last-Event-ID`) do: the page
  sends `forwardedProps.resume = {"after": n, "startedAt": t}`, the number
  of the run's events it has and its `RUN_STARTED` timestamp. The run's log
  sends `RUN_STARTED`, the start of whatever was open at that point (a text
  message, tool calls: `runs.reopened`), then the rest. `@ag-ui/client`
  checks each stream from its own start, and applies a start for a message
  or tool call it has by adding to it. A `RUN_STARTED` with another
  timestamp is another telling of the run (a microVM that started it
  again): the page takes back what it drew and draws that one.
- **Titles** (`titles.py`): when a conversation's first message runs, the
  stack's default model is asked for a 2–6 word title from it, beside the
  run rather than after it, as the Vercel chatbot does, so the answer isn't
  kept waiting. One small call, counted against the plan's tokens; the run
  waits at most 3 seconds for it after the answer, so the page's list,
  read again then, usually has it. If it fails, the first message stays the
  title; trying the first message again (or editing it) asks again. It never
  replaces a title the user chose.
- **Ratings**: thumbs up or down on an answer, on its last assistant message
  (`rating` on its `MSG#` item); `null` takes it back. Each is logged as
  `Answer rated`, with the workspace, user, conversation, message, rating and
  agents, for CloudWatch Logs Insights. Editing a message before the answer
  deletes the answer and its rating.
- **Stored** in the assistant table, under the user's own keys:
  `TENANT#<t>#USER#<u>#CONV#<threadId>` with `META` and `MSG#<n>` items, and
  the index `gsi1` for the list (`repository.py`). On a stack that had the
  assistant before, the deploy that adds `gsi1` finishes before DynamoDB has
  built it, and DynamoDB refuses to read an index it's still building: the
  list answers 500 for the first minutes (about 5 on dev, October 2026) while
  `IndexStatus` is `CREATING`. Chatting, reopening, and the history it writes
  meanwhile all work; the list then includes it.
- **Kept** until its user deletes it, or leaves the workspace (below), as in
  ChatGPT: the history is small (35 conversations in about 200 KB on dev,
  October 2026), and the large part, the agents' checkpoints, expires on its
  own (above). A stack can set `AssistantConversationDays` (template.yaml)
  instead: a conversation then goes whole that many days after its last
  message. Only its `META` has a TTL (`expires_at`, moved on by every
  message), so a long conversation never loses its oldest messages. Until
  TTL removes the `META` (within days), the app treats the conversation as
  gone, and a message sent to it is refused (404) rather than bringing it
  back. The removal reaches `purge.py` through the table's stream, filtered
  to TTL's removals of a `META`, and the worker deletes the messages and the
  agents' checkpoints. A stream batch that keeps failing goes to the purge
  dead-letter queue, whose alarm covers both. Set back to 0, the setting
  keeps a conversation that was to expire from its next message on.
  Messages saved before conversations expired whole (dev, October 2026)
  keep their own TTL and expire on their own.
- **Continuing one** sends the same threadId, so the agent picks up from its
  checkpoints, or, once they expired, from its side of the history. If its
  microVM stopped (15 minutes idle), a new one starts: the first answer is
  slower, and the old sandbox's files are gone.
- **Deleting** removes the history at once, then `purge.py` (an SQS queue and
  a Lambda, with a dead-letter queue and an alarm) deletes the agents' copy:
  each one's checkpoint thread, hundreds of records for a short conversation,
  and any of its checkpoints in S3. It runs in the background, and a retry
  continues where the last try stopped, since the checkpoints, which lead to
  the rest, go last. `checkpoints.py` follows the library's record layout with
  plain boto3 (the Lambda has no LangGraph); a test writes real checkpoints
  with `DynamoDBSaver` and checks the purge leaves nothing. Deleted while it
  answers, the run stops at its next chunk (or after the tool it is in) and
  writes its last checkpoint then, possibly after the purge ran: so once its
  answer can't be saved, the run deletes its own thread too (`app.forget`).
- **A member who leaves** the workspace, or is removed from it, loses their
  conversations there: accounts saves `MemberRemoved` with the removal (one
  transaction), EventBridge puts it on the purge queue, and the worker
  deletes each of their conversations as a delete would, checkpoints first.
  Only those last used before the removal: someone invited back keeps what
  they said since, even if the event came late. The member's ID token works
  until it expires (up to 15 minutes, as in every module): a conversation
  they use in that time, after the worker ran, is kept.

## Deploy

The template creates the assistant only when it gets the code's location, so
the rest of the stack deploys exactly as before without it. Once it exists, a
plain `sam deploy` keeps it at its last code (SAM reuses parameters you don't
pass); deploy with the command below to update it.

```bash
./scripts/export-requirements.sh && sam build
sam deploy --config-env dev \
  --parameter-overrides "$(uv run python scripts/package-assistant.py --config-env dev)"
```

`package-assistant.py` installs the locked dependencies as Linux arm64 wheels
(AgentCore's `PYTHON_3_13` is Amazon Linux 2023 on arm64), adds `shared/`,
`assistant/` and `main.py`, uploads the zip to the bucket `sam deploy` uses,
under its hash, and prints this environment's parameter overrides plus the
zip's location. Same code, same key: CloudFormation leaves the agent alone.

Once per account and region:

- **Model key**: the default model is `deepseek/deepseek-v4.1-flash-1017`
  through [OpenRouter](https://openrouter.ai) (pay as you go from prepaid
  credits); any OpenRouter model that supports tools works, such as
  `anthropic/claude-sonnet-5.5`.
  Store your OpenRouter API key as an SSM SecureString, under the name
  `OpenRouterApiKeyParam` gives in `samconfig.toml` (`read -s` keeps it out of
  your shell history):
  `read -rs key && aws ssm put-parameter --name /saas/dev/openrouter-api-key --type SecureString --value "$key"`.
  The agent reads it when it calls the model (cached 5 minutes, so a new key
  is picked up without a deploy). Requests go only to providers that don't
  collect prompts (OpenRouter's `data_collection: deny`).
- **Or Bedrock**: set `AssistantModelId` (or an agent's `model`) to a Bedrock
  id, such as `global.anthropic.claude-sonnet-5-5`, and enable the model in the
  Bedrock console. The model id picks the provider: `vendor/model` is OpenRouter.
- **Traces**: run `./scripts/enable-transaction-search.sh <region> [days]` once
  per account. It turns on CloudWatch Transaction Search, which X-Ray needs
  before it takes the runtime's spans (until then it answers each batch with a
  400, logged every 5 seconds), and keeps spans in `aws/spans` for `days`
  (default 7). Then the runtime's traces appear in the CloudWatch GenAI
  observability console (`opentelemetry-instrument` in the entry point).
- **Messages in logs**: ADOT's LangChain instrumentation logs each model
  call's messages (the conversation so far, the answer) to the runtime's log
  group (`otel-rt-logs`; it takes them out of the spans), and traces each
  model call and middleware step. Dev keeps both, to debug with; prod turns
  the instrumentation off (`AWS_GENAI_INSTRUMENTATION: disabled` in
  `template.yaml`), so a conversation is only where the app can delete it,
  and its traces show the calls to AWS, OpenRouter and the API.
- **Log retention**: AgentCore creates `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`
  itself, without one; `AssistantLogRetention` in `template.yaml` sets 7 days
  in dev (it holds the messages) and `LogRetentionDays` in prod.

Existing sessions keep the code they started with: a new session id gets new
code. Idle conversations stop their microVM after 15 minutes; the conversation
continues from its checkpoints in a new one.

## Run it locally

```bash
uv run pytest modules/assistant                    # everything offline: fake model, AWS (moto) and API
```

Against the dev stack's tables and bucket (your AWS credentials; the values in
`.env`, see `.env.example`, with `OPENROUTER_API_KEY`), from the repository root:

```bash
uv run python modules/assistant/main.py   # serves http://localhost:8080/invocations
curl -N localhost:8080/invocations -H "Authorization: Bearer <a JWT with your claims>" \
  -H "Content-Type: application/json" \
  -d '{"threadId": "<a UUID>", "runId": "r1", "forwardedProps": {"tenantId": "<a tenant id in the token>"},
       "messages": [{"id": "m1", "role": "user", "content": "Hi"}]}'
```

There is no authorizer locally, so the server trusts any token: never expose it.

## Operations

- **Cost per workspace**: each message logs one line, `Assistant answered`,
  with `tenant_id`, `user_id`, `session_id`, `input_tokens` and `output_tokens`.
  CloudWatch Logs Insights can sum them by `tenant_id`. The usage table has the
  monthly totals the plan limits use.
- **Messages count when they start**, even if the agent then fails. Tokens are
  counted per model call, as they are spent. An edit, Try again or
  Regenerate is a message too.
- **Ratings**: `Answer rated` lines (`rating` up or down, or null when taken
  back), in the HTTP API Lambda's log group, by `tenant_id`, `agents` and
  `session_id`. A thumbs down's conversation is there to read, while it
  lasts. A message refused at the hourly limit logs `Hourly message limit
  reached` there too, with the plan and the limit.
- **The checkpointer is required**: without it there is no conversation, so
  failing to read or write checkpoints fails the message (error event 500).
- **Removing the assistant** (deploying with empty `AssistantCodeBucket` and
  `AssistantCodeKey`) keeps its tables and checkpoint bucket, like every table
  here. Adding it back creates new ones, so usage counts start over.

## Why it's built this way (research, October 2026)

- **Runtime, not Lambda.** AgentCore Runtime hosts any framework (LangGraph
  included), isolates each session in a microVM, streams SSE, and keeps a
  session for up to 8 hours with a 15-minute idle timeout. Direct code
  deployment (a zip) needs no Docker and allows a higher new-session rate (25/s)
  than containers (about 1.6/s), within 250 MB zipped. A managed alternative,
  AgentCore *Harness* (GA June 2026), runs a configured loop (Strands) instead
  of your own LangChain code, so it doesn't fit here.
- **Auth.** The inbound JWT authorizer validates the Cognito token against the
  user pool's discovery URL, audience and custom claim rules before the code
  runs; the header allowlist forwards `Authorization`. This is the setup in AWS's
  pool-model multi-tenancy post. A runtime takes either JWT or SigV4, not both.
- **Quotas in two layers.** AgentCore has no per-tenant quota of its own. AWS's
  multi-tenant guidance enforces limits at several layers and meters tokens from
  the model's usage fields per tenant. Here: a conditional counter before the
  run (cheap rejection), middleware on every model call (the actual spend), and
  built-in call limits per message. The code samples from that post use API
  Gateway usage plans in front of a Lambda proxy instead; with the browser calling
  AgentCore directly, the counters do that job.
- **Checkpoints in DynamoDB, not AgentCore Memory.** `langgraph-checkpoint-aws`
  offers both. Measured on dev with `AgentCoreMemorySaver` (October 2026): the
  checkpoints were 49 times the size of the chat history (an 11-message
  conversation, 2.7 MB against 53 KB), one answer made 36 `CreateEvent` calls
  that added about a second of its 5, and short-term Memory is billed per GB
  since 6 October. `DynamoDBSaver` writes to a table in the stack, compressed,
  with a TTL and S3 for large checkpoints (AWS's DynamoDB guide for LangGraph).
  The long-term memory that came with Memory (facts and preferences recalled
  before each answer) went with it: those never expired and users couldn't see
  or delete them (see next steps).
- **Isolation.** Pool model: one runtime and one checkpoint table for every
  workspace. Every key includes `"<tenant_id>:<user_id>"` from the verified
  token: a conversation's keys, and the hash that names a checkpoint thread.
  IAM doesn't enforce it yet (one execution role): see next steps.
- **Lessons from people running it.** Keep `/ping` responsive (a blocked ping
  gets the session recycled: the entry point is a generator the SDK runs in a
  worker thread); unbuffered stdout or logs get lost when the microVM stops;
  first messages after idle pay a cold start (tens of seconds reported for
  heavy containers), so keep the package lean and build clients once per
  session; "checkpointing is not idempotency": tools that change data need
  idempotency keys.
- **Region.** ap-southeast-1 has Runtime, Memory, Gateway and Identity; per the
  regions page, not Policy or Evaluations.

### Next steps (not built yet)

- **IAM-enforced isolation**: a token-vending role with tenant session tags
  (ABAC), as in AWS's pool-model post, so a bug can't read another tenant's
  conversations.
- **Long-term memory across conversations**, if wanted again: `DynamoDBStore`
  (semantic search on DynamoDB vector indexes, same package) or AgentCore
  Memory's strategies, with an expiry and a page where users see and delete
  what is remembered about them.
- **Tools that change data**: idempotency keys plus `HumanInTheLoopMiddleware`
  (needs a resume flow in the frontend).
- **Terminal data in and out**: a tool that writes workspace data straight into
  the sandbox (Code Interpreter `writeFiles`), so the model doesn't retype it;
  and an S3 bucket the sandbox can write to, one prefix per tenant, for files
  and charts the user can download.
- **More tools or third-party APIs**: AgentCore Gateway (MCP) with interceptors,
  and AgentCore Identity for OAuth on the user's behalf.
- **Guardrails** (Bedrock Guardrails) for prompt injection and PII; tool output
  is user-typed data too.
- **Long conversations**: older messages are dropped from what the model sees,
  not summarized. `SummarizationMiddleware` could keep their gist (its model
  calls would need to count against the token cap too). Each checkpoint holds
  the whole conversation, so a long one writes more with every message.
- **Conversations**: older messages of a conversation past 200 (open returns
  the latest 200), search, and branches (an edit replaces what came after
  it; ChatGPT keeps both as a tree).
- **Ratings with a reason**: a comment on a thumbs down, and an evaluation
  set built from rated answers (AgentCore Evaluations isn't in
  ap-southeast-1 yet).

### Sources

- [AgentCore overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html), [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html), [troubleshooting](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-troubleshooting.html), [InvokeAgentRuntime](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html)
- [AG-UI on AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-agui.html), [its protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-agui-protocol-contract.html) (errors as `RUN_ERROR`), [AG-UI events](https://docs.ag-ui.com/concepts/events)
- [Direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html), [for Python](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html), [supported runtimes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-code-deploy-supported-runtimes.html)
- [Inbound JWT authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html), [header allowlist](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html), [execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html)
- [DynamoDB as a checkpoint store for LangGraph](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ddb-langgraph-checkpoint.html), [AgentCore Memory with LangGraph](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html) (what it replaced), [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/), [langchain-aws repository](https://github.com/langchain-ai/langchain-aws)
- [LangChain custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom), [runtime context](https://docs.langchain.com/oss/python/langchain/runtime)
- AWS ML blog: [Building multi-tenant agents with AgentCore](https://aws.amazon.com/blogs/machine-learning/building-multi-tenant-agents-with-amazon-bedrock-agentcore/) (May 2026), [Pool model multi-tenancy with AgentCore](https://aws.amazon.com/blogs/machine-learning/shared-infrastructure-isolated-tenants-pool-model-multi-tenancy-with-amazon-bedrock-agentcore/) (June 2026), [custom domains (CloudFront, CORS)](https://aws.amazon.com/blogs/machine-learning/set-up-custom-domain-names-for-amazon-bedrock-agentcore-runtime-agents/)
- [Supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
- Community: [AgentCore deployment, operations and lessons learned](https://www.letsdodevops.com/p/agentcore-deployment-operations-and), [LangGraph builds the agent, AgentCore runs it](https://dev.to/rajmurugan/langgraph-builds-the-agent-agentcore-runs-it-1hg7), [production-ready agents with LangGraph and AgentCore](https://dev.to/aws/building-production-ready-ai-agents-with-langgraph-and-amazon-bedrock-agentcore-4h5k)
