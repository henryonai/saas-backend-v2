# Assistant module (AI agents on Amazon Bedrock AgentCore)

LangChain agents (`create_agent`) that answer signed-in users about their
workspace, **each defined in a Markdown file** (Claude Code's subagent
format). They run on **AgentCore Runtime** (one microVM per conversation)
instead of Lambda, keep conversations and long-term memories in **AgentCore
Memory**, and use the same Cognito ID token, plan catalog and module rules as
the rest of the backend.

```
browser ──ID token──▶ AgentCore Runtime ── JWT authorizer (user pool, aud, token_use=id)
                        │  POST /invocations {"prompt", "agent"?}, SSE back
                        ▼
                 app.py  handle()
                   1. which agent (agents/*.md; "assistant" by default)
                   2. claims → CurrentUser (shared.auth rules)
                   3. UsageService.start_message → 402 when the month's allowance is used
                   4. agent.stream(...)   built by factory.py from its Markdown file
                        ├─ UsageMiddleware         tokens of every model call → DynamoDB, stop at the cap
                        ├─ ConversationMiddleware  repair cut-off tool calls; send recent history only
                        ├─ PlanToolsMiddleware     hide tools the plan doesn't include
                        ├─ MemoryMiddleware        recall → system prompt; save the exchange
                        ├─ Model/ToolCallLimit, ToolErrorMiddleware (built-in)
                        ├─ checkpointer      AgentCoreMemorySaver (each agent's side of the conversation)
                        ├─ tools ──user's own token──▶ HTTP API (/orders ...)
                        ├─ bash ──InvokeCodeInterpreter──▶ sandbox microVM (one per conversation,
                        │                                   no AWS role, no internet by default)
                        └─ task ──▶ a subagent (fresh context, same user and sandbox, no checkpoints)
```

## Files

```
src/assistant/
├── agents/              one Markdown file per agent: the part you edit most
│   ├── assistant.md     the main agent (the frontend's default)
│   └── analyst.md       a subagent the assistant delegates analysis to
├── definitions.py       reads and checks agents/*.md (at startup)
├── factory.py           definitions → running agents: model, tools, middleware, subagents
├── delegation.py        the task tool: an agent runs one of its subagents
├── tools/               the tools agents can name in `tools:` (a registry in __init__.py)
│   ├── orders.py        list_orders, get_order (the HTTP API, with the user's token)
│   ├── usage.py         assistant_usage
│   └── bash.py          bash (the sandbox)
├── middleware/          one file per middleware (usage, conversation, plan_tools, memory, errors)
├── app.py               AgentCore entry point (BedrockAgentCoreApp): agent, auth, quota, stream
├── context.py           AgentContext: who the agents work for (runtime context, never in a prompt)
├── schemas.py           the request and the streamed events: the frontend contract
├── memory.py            AgentCore Memory adapter: recall() / remember()
├── sandbox.py           AgentCore Code Interpreter adapter: one sandbox per conversation
├── service.py, repository.py, models.py   monthly usage per workspace (DynamoDB counters)
└── container.py         composition root (OpenRouter or Bedrock models, Memory, DynamoDB, httpx)
../main.py               what AgentCore starts (root of the zip)
```

Each layer knows only the one below it: agents (Markdown) name tools and
subagents; the factory wires them with middleware; tools and middleware use
the service and the adapters; only `container.py` creates real AWS clients.

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
agent's words (`{"type":"tool","name":"task"}` marks the hand-off). Two
differences, both to bound a message's cost: an agent may only call the
subagents it lists, and a subagent can't delegate further (one level). A
subagent keeps no checkpoints and no long-term memory, so delegating adds
model tokens (counted against the same allowance) but no Memory writes.

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

**Before the agent runs** (cheap, no model call), in `app.handle()`:

1. The token's claims become a `CurrentUser` with `shared.auth.user_from_claims`,
   the same rules as every HTTP route (ID tokens only, workspace claims all or
   none). AgentCore verified the signature, issuer, audience and `token_use`
   already, and forwards the `Authorization` header (`RequestHeaderAllowlist`).
2. `UsageService.start_message()` counts the message in one conditional
   DynamoDB update, like orders' plan limit (rule 8): it fails, with 402, when
   the workspace is out of messages **or** tokens for the month. The plan comes
   from the token. Limits live in `shared/plans.py`
   (`assistant_messages_per_month`, `assistant_tokens_per_month`).

**While it runs**, in middleware (`middleware/`, in the order `factory.py` sets):

- `UsageMiddleware.wrap_model_call` adds each model call's `usage_metadata` to
  the month's tokens, read from the model's response itself (it is the outermost
  wrapper; `after_model` would see whatever other middleware appended first).
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
- `MemoryMiddleware.before_agent` searches AgentCore Memory once per message
  (`/actors/<tenant_id>:<user_id>/`), `wrap_model_call` adds the results to the
  system prompt (never to the messages, so they don't pile up), and
  `after_agent` saves the user's words and the final answer as a conversational
  event. AgentCore extracts facts and preferences from those in the background
  (strategies in `template.yaml`).
- Built-in `ModelCallLimitMiddleware` (the agent's `maxTurns`, default 15) and
  `ToolCallLimitMiddleware` (15) bound one run, whatever the model decides; a
  subagent's run has its own. `ToolErrorMiddleware` turns a
  tool's exception into a result the model can explain (details only in the
  logs) instead of failing the message.

The conversation itself is checkpointed by `AgentCoreMemorySaver`. Its thread
id is a hash of user and session (`thread_config()`): the client picks the
session id, so someone sending another user's session id still gets their own
conversation, whatever checkpointer is used.

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

`POST <AssistantUrl output>` with:

```
Authorization: Bearer <Cognito ID token>          (the same token the HTTP API takes)
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <one UUID per conversation; 33+ characters>
Content-Type: application/json

{"prompt": "What did we order last week?"}          (add "agent": "<name>" for another agent)
```

The answer streams as server-sent events, one JSON object per `data:` line:
`{"type":"text","text":...}` (append them), `{"type":"tool","name":...}`
(show "looking that up..."), then `{"type":"done",...}` or
`{"type":"error","status":402,"message":...}`.

**Errors are events, not statuses.** AgentCore turns any 4xx/5xx the agent
returns into a generic `424 RuntimeClientError`, so the useful status (402:
show "Upgrade"; 401/403: sign in again) is in the event. AgentCore's own errors
(401 bad token, 429 throttling) do arrive as HTTP statuses.

`EventSource` can't send headers or POST: read the stream with `fetch`:

```ts
const res = await fetch(assistantUrl, {
  method: "POST",
  headers: { Authorization: `Bearer ${idToken}`, "Content-Type": "application/json",
             "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": conversationId },
  body: JSON.stringify({ prompt }),
});
const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
// split on "\n\n", strip "data: ", JSON.parse each event
```

Not verified yet: whether the AgentCore endpoint answers a browser's CORS
preflight. Test it from your app's origin first. If it doesn't, AWS's pattern
is CloudFront in front of the runtime with a CORS response headers policy
(see Sources), which also gives you a custom domain.

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
- **Traces**: enable CloudWatch Transaction Search (AgentCore observability
  guide), then traces of every model and tool call appear in the CloudWatch
  GenAI observability console (`opentelemetry-instrument` in the entry point).
- **Log retention**: AgentCore creates `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`
  without one: set it with `aws logs put-retention-policy`.

Existing sessions keep the code they started with: a new session id gets new
code. Idle conversations stop their microVM after 15 minutes; the conversation
continues from Memory in a new one.

## Run it locally

```bash
uv run pytest modules/assistant                    # everything offline: fake model, Memory and API
```

Against the dev stack's table and Memory (your AWS credentials; the values in
`.env`, see `.env.example`, with `OPENROUTER_API_KEY`), from the repository root:

```bash
uv run python modules/assistant/main.py   # serves http://localhost:8080/invocations
```

There is no authorizer locally, so the server trusts any token: never expose it.

## Operations

- **Cost per workspace**: each message logs one line, `Assistant answered`,
  with `tenant_id`, `user_id`, `session_id`, `input_tokens` and `output_tokens`.
  CloudWatch Logs Insights can sum them by `tenant_id`. The usage table has the
  monthly totals the plan limits use.
- **Messages count when they start**, even if the agent then fails. Tokens are
  counted per model call, as they are spent.
- **Memory is best effort**: if AgentCore Memory fails, the agent answers
  without memories and logs a warning. The checkpointer is not: without it
  there is no conversation, so that fails the message (error event 500).
- **Removing the assistant** (deploying with empty `AssistantCodeBucket` and
  `AssistantCodeKey`) keeps its table and Memory, like every table here. To add
  it back, first delete the retained Memory (`saas_<stage>_assistant`, its name
  is fixed) or the deploy fails; the table is created new, so usage counts
  start over.

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
- **Memory as middleware.** `langgraph-checkpoint-aws` provides
  `AgentCoreMemorySaver` (checkpoints as blob events) and `AgentCoreMemoryStore`;
  AWS's LangChain guide saves messages from a pre-model hook and searches
  memories to add context. Checkpoint blobs are not used for long-term
  extraction, only conversational events are, hence `remember()`. Common advice:
  save the user's original words, not the prompt with memories added.
- **Isolation.** Pool model: one runtime and one Memory for every workspace,
  scoped by `actor_id = "<tenant_id>:<user_id>"` from the verified token. AWS's
  posts use the same composite actor id. IAM doesn't enforce it yet (one
  execution role): see next steps.
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

- **IAM-enforced memory isolation**: a token-vending role with tenant session
  tags (ABAC), as in AWS's pool-model post, so a bug can't read another
  tenant's memories.
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
  calls would need to count against the token cap too). In its default format,
  `AgentCoreMemorySaver` also reads the whole thread on every message, so the
  frontend should start a new conversation (session id) now and then.

### Sources

- [AgentCore overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html), [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html), [troubleshooting](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-troubleshooting.html), [InvokeAgentRuntime](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html)
- [Direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html), [for Python](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html), [supported runtimes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-code-deploy-supported-runtimes.html)
- [Inbound JWT authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html), [header allowlist](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html), [execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html)
- [Memory with LangChain/LangGraph](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html), [memory organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-organization.html), [langchain-aws repository](https://github.com/langchain-ai/langchain-aws)
- [LangChain custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom), [runtime context](https://docs.langchain.com/oss/python/langchain/runtime)
- AWS ML blog: [Building multi-tenant agents with AgentCore](https://aws.amazon.com/blogs/machine-learning/building-multi-tenant-agents-with-amazon-bedrock-agentcore/) (May 2026), [Pool model multi-tenancy with AgentCore](https://aws.amazon.com/blogs/machine-learning/shared-infrastructure-isolated-tenants-pool-model-multi-tenancy-with-amazon-bedrock-agentcore/) (June 2026), [custom domains (CloudFront, CORS)](https://aws.amazon.com/blogs/machine-learning/set-up-custom-domain-names-for-amazon-bedrock-agentcore-runtime-agents/)
- [Supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
- Community: [AgentCore deployment, operations and lessons learned](https://www.letsdodevops.com/p/agentcore-deployment-operations-and), [LangGraph builds the agent, AgentCore runs it](https://dev.to/rajmurugan/langgraph-builds-the-agent-agentcore-runs-it-1hg7), [production-ready agents with LangGraph and AgentCore](https://dev.to/aws/building-production-ready-ai-agents-with-langgraph-and-amazon-bedrock-agentcore-4h5k)
