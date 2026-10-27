# SaaS backend (multi-tenant, serverless)

Python on AWS Lambda, with Lambda Powertools, DynamoDB, Cognito and EventBridge.
Four self-contained modules: **accounts, orders, notifications, billing**, plus
an **assistant** (a LangChain agent on Amazon Bedrock AgentCore, see
[modules/assistant/README.md](modules/assistant/README.md)).
Orders is the sample module: a small domain module to copy for your own.

## Starting a new project from this template

1. Copy the repository (GitHub's "Use this template", or clone it into a new
   repository).
2. Give it its own name. This renames the packages, stacks, SSM parameter
   names, the event source and the browser storage keys, so two projects never
   collide in one AWS account:

   ```bash
   uv run python scripts/rename-project.py acme --title "Acme"   # add --dry-run to preview
   uv sync --all-packages && (cd web && pnpm install)
   git diff                                                      # review, then commit
   ```

3. Create the stack's webhook secret ("Configuration and secrets"), deploy
   ("Deploy"), and set up the deploy workflow ("CI/CD").
4. Build your first module from the sample, then remove the sample
   ("The sample module").

## Layout

```
saas-backend-v2/
├── pyproject.toml              # uv workspace root: dev tools, pytest and ruff config
├── uv.lock                     # exact versions of every package (commit it)
├── .python-version             # 3.13, same as the Lambda runtime
├── .github/
│   ├── workflows/ci.yml        # tests, lint, lockfile and requirements checks
│   ├── workflows/deploy.yml    # deploy an environment (run by hand)
│   └── deploy-role.yaml        # the AWS role deploy.yml uses (once per environment)
├── scripts/
│   ├── export-requirements.sh  # uv.lock -> requirements.txt files for sam build
│   ├── changeset-summary.py    # a change set in Markdown, for reviewing prod deploys
│   ├── smoke-test.py           # end-to-end check of a deployed stack (after sam deploy)
│   ├── stripe-setup.py         # Stripe prices, portal and webhook for a stack
│   ├── export-openapi.py       # each module's OpenAPI document -> docs/openapi/
│   ├── package-assistant.py    # the assistant's arm64 code zip -> S3, for sam deploy
│   ├── enable-transaction-search.sh  # once per account: the assistant's traces in CloudWatch
│   ├── check-assistant-data.py # the assistant's table against its data model (read-only)
│   ├── dev-pay.py              # finish a dev checkout: send the signed payment webhook
│   ├── replay-outbox.py        # publish saved events again after an outage
│   ├── migrate-memberships.py  # once, for stacks from before users had several workspaces
│   └── rename-project.py       # give a new copy of the template its own name
├── layers/shared/              # package "saas-shared"
│   ├── pyproject.toml          # powertools, pydantic, pydantic-settings
│   ├── layer/                  # deployed as a Lambda layer
│   │   ├── requirements.txt    # generated, do not edit
│   │   └── shared/             # plumbing only, no business logic
│   │       ├── auth.py         # CurrentUser + get_current_user (Depends)
│   │       ├── errors.py       # NotFound, Forbidden, PlanLimitReached ...
│   │       ├── events.py       # Event + EventBridge publisher
│   │       ├── outbox.py       # saves events with the change; relay publishes them
│   │       ├── dynamo.py       # transactions with named conditions
│   │       ├── http.py         # create_app / create_handler
│   │       ├── ids.py          # ids that sort by time
│   │       ├── plans.py        # plan catalog and limits
│   │       └── settings.py     # BaseAppSettings (pydantic-settings)
│   └── tests/
├── modules/
│   └── <module>/               # package "saas-<module>", close to a ready-made repository
│       ├── pyproject.toml      # depends on saas-shared; add module-only packages here
│       ├── src/requirements.txt  # generated, do not edit
│       ├── src/<module>/
│       │   ├── settings.py     # this module's config (env vars)
│       │   ├── models.py       # what the module stores and works with
│       │   ├── schemas.py      # what the HTTP API accepts and returns
│       │   ├── repository.py   # the only code touching this module's table
│       │   ├── service.py      # business rules; all dependencies passed in
│       │   ├── events.py       # events this module publishes (its contract)
│       │   ├── container.py    # builds the service once (composition root)
│       │   ├── api.py          # HTTP routes, using Depends()
│       │   ├── handler.py      # Lambda entry point for the HTTP API
│       │   └── ...             # other entry points (worker, webhook, triggers)
│       └── tests/
│   (modules/assistant's chat runs on AgentCore Runtime: app.py, with its
│    agents as Markdown files in src/assistant/agents/; its handler.py serves
│    the users' conversations on the HTTP API)
├── testkit/                    # package "saas-testkit": test helpers and fakes (not deployed)
├── web/                        # the React app (see web/README.md)
├── template.yaml               # one section per module
├── samconfig.toml              # dev / prod settings
└── .env.example
```

## The rules

1. **No module imports another module.** They communicate through the login
   token and events only.
2. **Each module owns its table.** Nobody reads another module's table.
3. **Services receive every dependency in their constructor** (no defaults,
   no globals). `container.py` is the only place that creates real ones.
4. **Routes get what they need through `Depends()`**: the current user and
   the service. Workers, webhooks and triggers call the container directly.
5. **Only `container.py` reads settings.** Everything else gets plain values.
6. **The workspace comes from the login token, never from the request body.**
   The token lists every workspace the user belongs to; a request only picks
   one of them (the `X-Tenant-Id` header), and `get_current_user` refuses
   any other. Request schemas never contain `tenant_id`.
7. **Each module defines the events it publishes in its `events.py`**, and
   saves them with the change, never sends them directly. The repository
   writes the event into its table in the same transaction (the outbox), and
   the relay publishes it. No change without its event, no event without its change.
8. **Rules that must survive concurrent requests are DynamoDB conditions**
   (plan limits, workspaces per user, allowed status changes), not
   "read, check, then write" code.
9. **Listeners expect repeats and late arrivals.** They skip events they have
   seen (`event_id`) and ignore older versions (`revision`).

## Models, schemas and events

Each module keeps three kinds of Pydantic classes apart, so one can change
without breaking the others:

| File | What | Used by |
|---|---|---|
| `models.py` | what the module **stores** and works with (`Tenant`, `Order`) | repository, service, events |
| `schemas.py` | what crosses **HTTP**: request bodies and responses (`OrderCreate`, `TenantOut`) | api.py; service inputs |
| `events.py` | what crosses **to other modules** | service |

The repository never imports `schemas.py`. The service takes request schemas
and returns models; `api.py` turns models into response schemas. When a
response is identical to a model, the route returns the model; add an `...Out`
schema as soon as they differ (e.g. `TenantOut` hides internal bookkeeping
fields such as `subscription_revision`).

## How modules talk to each other

| Need | Pattern | Example |
|---|---|---|
| Facts about the caller | Login token | Orders reads `plan` from the token to check its limit |
| Something happened | Event | Orders saves `OrderCreated`; notifications reacts |
| Listener needs details | Fat event | Billing's events include `owner_email`, so notifications can email without asking accounts |
| Need another module's data often | Local copy via events | Accounts keeps a copy of the plan, updated by `SubscriptionChanged` |
| Act for the user | Public HTTP API, with the user's own token | The assistant's tools list orders through `/orders` |

**Events** (each one also carries `tenant_id`, `event_id` and `occurred_at`)

| Event | Published by | Used by |
|---|---|---|
| `UserInvited` | accounts | notifications (invitation email) |
| `MemberRemoved` | accounts | assistant (deletes the member's conversations, memories and files in that workspace) |
| `OrderCreated` | orders | notifications (in-app) |
| `SubscriptionChanged` (with `revision`) | billing | accounts (plan copy), notifications |
| `PaymentFailed` | billing | notifications |

**How an event travels:** service creates an `Event` → repository saves it as
an `OUTBOX#` item in the same transaction as the change (kept 7 days) → the
table's DynamoDB stream triggers `OutboxRelayFunction` → it publishes to this
stack's event bus, retrying until it works → a rule sends it to the
listener's SQS queue → the listener's worker (with a dead-letter queue, which
also gets what the rule couldn't deliver within an hour).
Delivery is at least once and not in order, hence rule 9.

## Main flows

**Sign-up:** user signs up in Cognito (verifies their email) → `POST /tenants`
creates the workspace and makes them `owner` → frontend refreshes the token →
the login trigger lists the workspace in the token's `workspaces` claim, with
their role there and its plan.

**Several workspaces:** a user can belong to up to 20 workspaces
(`MAX_WORKSPACES_PER_USER`): create more with `POST /tenants`, or accept
invitations, without leaving any. `GET /tenants` lists them, and the token
lists them all. Each request names the workspace it acts in with the
`X-Tenant-Id` header (the assistant: `forwardedProps.tenantId`), so each
browser tab can work in a different one. A workspace the token doesn't list
(joined after the token was issued) gets 401: refresh the token and retry.

**Invite:** an owner or admin (with a verified email) calls `POST /users/invite`
→ a pending invitation is saved (valid 7 days; at most 20 sent per workspace
per day, and at most 5 open invitations per email) → notifications emails a
link to the app → the person signs in, or signs up, **with that email** →
`GET /invitations` shows their invitations → they choose
`POST /invitations/<tenant_id>/accept` (takes a seat if the plan has one
left; then refresh the token) or `.../decline`. Nobody joins a workspace
without accepting, and nobody gets a login they didn't ask for. Admins see
pending invitations at `GET /users/invitations` and cancel one with
`DELETE /users/invitations/<email>`. A member leaves with
`DELETE /users/<their own id>`: they stay signed in, and their next token no
longer lists that workspace.

**Upgrade:** owner calls `POST /billing/checkout` → pays on the provider's page
(Stripe Checkout; a subscriber who changes plan gets Stripe's confirm page) →
provider calls `/webhooks/payments` → signature checked → billing applies
each field (plan, status) only if this event is newer than the one that last
set it (providers retry and don't keep order), saving the change and
`SubscriptionChanged` in one transaction → accounts updates its plan copy if the `revision` is newer, and
notifications tells the owner → after the next token refresh, the token
carries the new plan. The frontend should refresh the token after checkout.

## Security model

- **Tokens:** the frontend sends the Cognito **ID token** (it carries the
  custom `workspaces` claim) and the workspace it works in (`X-Tenant-Id`).
  Access tokens are rejected (401), and so are a `workspaces` claim the
  login trigger didn't write and a workspace the token doesn't list. ID
  tokens last **15 minutes**; refresh tokens 30 days.
- **Roles change immediately where it matters:** the accounts module checks
  the caller's role in its own table, not in the token, for everything it
  does. Removing someone or changing their role also signs them out of every
  workspace (refresh tokens revoked); someone who leaves stays signed in.
  Other modules trust the token, so there a change applies within 15
  minutes. Shorten `IdTokenValidity` if that is too long for you.
- **Emails are verified:** users can't switch their email to an address they
  haven't verified, and inviting or accepting an invitation needs a verified
  email. The invitation email quotes the workspace name (typed by the inviter)
  and says nothing happens unless the person accepts.
- **Tenant isolation is enforced in code:** every key starts with the
  `tenant_id` from the token. For a second, IAM-enforced layer, AWS's SaaS
  guidance uses tenant-scoped credentials with the `dynamodb:LeadingKeys`
  condition; add it if your customers need that assurance.
- **Least privilege:** each function can only touch its own table (some only
  read or update), only the relay can publish events, and only the webhook
  function can read the webhook signing secret (the billing API can read the
  Stripe key, to start checkouts).
- **The assistant** takes the same ID tokens (AgentCore Runtime's JWT
  authorizer checks the user pool, audience and `token_use`), counts every
  message and model token against the plan, keeps each user's conversations
  and memories under `<tenant_id>:<user_id>`, and reaches workspace data only
  through the HTTP API with the caller's token. Its terminal (bash tool) runs
  in a separate AgentCore Code Interpreter sandbox per conversation, with no
  AWS role and, by default, no internet.

## Dependency injection, in one example

```python
# container.py: built once per Lambda instance
@lru_cache
def get_order_service() -> OrderService:
    s = get_settings()
    return OrderService(orders=OrderRepository(table(s.orders_table)))

# api.py: the route declares what it needs
@router.post("/orders")
def create_order(body: OrderCreate,
                 user: Annotated[CurrentUser, Depends(get_current_user)],
                 service: Annotated[OrderService, Depends(get_order_service)]): ...

# tests: swap it, no monkeypatching
app.dependency_overrides[get_order_service] = lambda: OrderService(OrderRepository(fake_table))
```

## Configuration and secrets

- Settings classes (`settings.py`) read environment variables set in `template.yaml`.
  A missing value fails at cold start with a clear message.
- **Secrets are not environment variables.** The payment webhook secret lives
  in SSM Parameter Store; only its *name* is in the environment, and only the
  webhook function can read it (cached for 5 minutes, so rotation needs no
  redeploy). Create it once per environment before deploying:

  ```bash
  aws ssm put-parameter --name /saas/dev/payment-webhook-secret \
      --type SecureString --value "$(openssl rand -hex 32)"
  ```
- **Stripe** keeps its secret key in SSM the same way ("Payments").
- **Email:** set `EmailSender` and `SesIdentity` (the domain or address
  verified in SES) to send app emails *and* Cognito's sign-up codes through
  SES. Without them, app emails are only logged and Cognito uses its built-in
  sender, which allows about 50 emails a day.
- For local runs, copy `.env.example` to `.env` (never commit `.env`). Tests
  ignore it.
- Dev and prod run the same code with different values (`samconfig.toml`).
  Each stack has its own resources, names, event bus and secret, so both can
  live in one AWS account, as they do by default ("Before production" says
  when a second account is worth it).

## Payments

Billing talks to its payment provider through one interface
(`billing/provider.py`), picked per environment by `PaymentProvider`:

| | `dev` | `stripe` |
|---|---|---|
| Checkout | The app's `/dev-checkout` page | Stripe Checkout |
| Webhooks | Signed by `scripts/dev-pay.py` | Sent by Stripe |
| Card, invoices, plan changes, cancelling | Only through webhooks | Stripe's customer portal ("Manage billing") |
| Used by | Local runs and unit tests | Dev and prod (test mode until you launch) |

Setting up Stripe for a stack:

1. Put the Stripe secret key in SSM yourself, so it is never shown or stored
   anywhere else (a test key `sk_test_...`, or a restricted key):

   ```bash
   read -rs STRIPE_KEY && aws ssm put-parameter --name /saas/dev/stripe-secret-key \
       --type SecureString --value "$STRIPE_KEY"; unset STRIPE_KEY
   ```

2. Deploy the stack with `PaymentProvider=stripe` (`samconfig.toml`).
3. Run `uv run python scripts/stripe-setup.py --stack saas-dev`. It creates
   the prices (from `plans.py`), the customer portal and the webhook
   endpoint, and saves the webhook's signing secret and the prices in SSM.
   Run it again after changing the plans: it updates in place. The functions
   read the new values within 5 minutes.
4. Check it end to end with `uv run scripts/smoke-test.py --stack saas-dev`,
   which pays with Stripe's test cards.

How billing uses Stripe (`billing/stripe_provider.py`):

- A workspace's first subscription goes through Checkout, with its
  `tenant_id` in the subscription's metadata. Plan changes after that go
  through the portal's confirm page (prorated, with card authentication), so
  a workspace never has two subscriptions.
- The webhook checks Stripe's signature and its timestamp (5 minutes), then
  fetches the subscription, so billing saves Stripe's current state in
  whatever order events arrive. Updates that change nothing (Stripe sends
  many) publish nothing.
- `active` and `trialing` set the plan; `past_due` and `unpaid` are a failed
  payment (the plan stays); `canceled` means Free. Cancelling in the portal
  takes effect at the end of the paid period.

Going live: replace the key in `/saas/prod/stripe-secret-key` with the live
key and run `stripe-setup.py --stack saas-prod` again; live mode gets its own
prices, portal and webhook. Not covered yet: taxes, yearly prices, trials and
coupons.

## Dependencies (uv)

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/):
the shared layer, each module and the testkit are packages, with one `uv.lock`
for all of them.

```bash
uv sync --all-packages                  # install everything (plain `uv sync` skips the modules)
uv add --package saas-billing stripe    # a package only billing needs
uv add --package saas-shared httpx      # a package every module gets (through the layer)
./scripts/export-requirements.sh        # after any change above, before sam build
```

`sam build` installs dependencies with pip from `requirements.txt`, so the
script writes those files from `uv.lock`: exact versions for the layer, and for
each module only what it adds on top of the layer. Commit them together with
`uv.lock`; CI runs `./scripts/export-requirements.sh --check` and fails if
they are out of date. boto3 is not bundled because the Lambda runtime provides it.

## Tests and checks (offline)

```bash
uv sync --all-packages
uv run pytest                   # everything
uv run pytest modules/orders    # one module
uvx ruff check .                # lint
uvx cfn-lint template.yaml      # template
uv run python scripts/export-openapi.py   # OpenAPI documents for the frontend
```

## Deploy

```bash
./scripts/export-requirements.sh
sam build
sam deploy --config-env dev
sam sync --config-env dev   # fast code sync while developing
sam deploy --config-env prod
```

With the assistant, package its code first and pass its location (without it,
a new stack has no assistant, and an existing one keeps the code it has):

```bash
sam deploy --config-env dev \
  --parameter-overrides "$(uv run python scripts/package-assistant.py --config-env dev)"
```

Once per AWS account, for the assistant's traces:
`./scripts/enable-transaction-search.sh <region>` (see modules/assistant/README.md).

A stack deployed before users could belong to several workspaces keeps each
user's workspace in a `MEMBERSHIP` item. Move them once, around the first
deploy with the new code (a new stack doesn't need this):

```bash
uv run python scripts/migrate-memberships.py copy --stack saas-dev      # before deploying
uv run python scripts/migrate-memberships.py cleanup --stack saas-dev   # after
```

Outputs: `ApiUrl`, `UserPoolId`, `UserPoolClientId` (for the React app),
`WebhookUrl` (for the payment provider), `EventBusName`, `AlertsTopicArn`,
`WebUrl`, `WebBucketName`, `WebDistributionId` (where `web/scripts/deploy.sh`
publishes the app), and `AssistantUrl` when the assistant is deployed.

The tables and the user pool are kept when the stack is deleted (and can't be
deleted at all in prod), so data survives mistakes. Delete them by hand if you
really want a dev stack gone.

After deploying, check the stack end to end:

```bash
uv run scripts/smoke-test.py --stack saas-dev
```

It creates three test users and calls every endpoint: workspaces, orders,
billing (upgrade, payment failed, cancel; through Stripe's test mode on a
Stripe stack), invitations, roles,
leaving and removing members, tenant isolation and plan limits, and it waits
for the events to reach the accounts and notifications workers. It needs AWS
credentials for the stack (to create the users and read the webhook secret
or the Stripe key),
so use it on dev, not prod. The test users' logins are saved in
`.aws-sam/smoke-users.json`.

With the assistant deployed, `uv run scripts/smoke-assistant.py --stack saas-dev`
checks it the same way, with the stack's real model, AgentCore and S3, and
after each step reads what the assistant table and the files bucket hold: a
conversation created, read, renamed, rated and deleted (its checkpoints
purged), versions and how `META` stores them, Stop in the middle of an
answer, an answer asked for again that fails, a long one read back, memory,
files (uploads, what S3 refuses, attachments the model reads, files it
writes, versions, links, deletes), on a workspace it sets to Pro the
sandbox's files, and a member who leaves (the purge deletes all of theirs).
It adds three test users and costs a few cents of model tokens.

## Frontend

`web/` is the React app (Vite, TanStack Router and Query, shadcn/ui, English
and Vietnamese). It signs in with Cognito and calls the HTTP API with the ID
token, so it needs a deployed stack:

```bash
cd web
pnpm install
./scripts/env-from-stack.sh saas-dev   # ApiUrl, UserPoolId, UserPoolClientId -> .env.local
pnpm dev                               # http://localhost:5173
./scripts/deploy.sh saas-dev           # publish it at the stack's WebUrl (S3 + CloudFront)
```

Its API types and plan catalog are generated from this code (`pnpm gen:api`);
CI fails if they are out of date. With `PaymentProvider=dev`, checkout lands on
the app's `/dev-checkout` page, which shows the `scripts/dev-pay.py` command
that sends the signed webhook a real provider would send. See
[web/README.md](web/README.md).

## CI/CD

**CI** (`.github/workflows/ci.yml`) runs on every pull request and push to
`main`: the backend's tests and linters, and the frontend's typecheck, lint,
tests and build.

**CD** (`.github/workflows/deploy.yml`) is run by hand for now: on GitHub,
Actions > deploy > Run workflow, then pick the branch and the environment.
It runs the CI checks, then `sam deploy` (with the assistant's code zip from
`scripts/package-assistant.py`, as above) and `web/scripts/deploy.sh` for that
environment (its `[<env>.deploy]` settings in `samconfig.toml`). Prod deploys
only from `main`, one deploy per environment runs at a time, and the run's
summary links the app. In an account where `sam deploy` has never run, the
first run has no bucket for the zip yet: it deploys without the assistant's
new code and says so, and the next run adds it.

Prod deploys in two runs, so someone reads the changes before they happen:

1. **Prepare:** run it for prod. It creates a CloudFormation change set, lists
   every change in the run's summary (`scripts/changeset-summary.py`), with
   the resources it would replace in bold, and stops. Nothing changes yet.
2. **Apply:** run it for prod again with *apply* set to the first run's
   number. It applies exactly that change set, then publishes the web app.
   It refuses if `main` has moved since (prepare again) or if a newer run
   prepared another deploy.

Dev deploys in one run; tick *prepare* to use the two runs there too. The
prepared deploy is kept in the SSM parameter `/github-deploy/<stack>/prepared`
until it is applied. If a first deploy fails, CloudFormation leaves the stack
in `ROLLBACK_COMPLETE`: delete the stack, then prepare again.

`main` only changes through pull requests whose CI checks pass (a branch
protection rule), so a prod deploy always starts from checked code.

GitHub signs in to AWS with OIDC, so no AWS keys are stored in GitHub. Once
per environment, in the account it deploys to:

1. Create its deploy role. It may do everything except IAM, and in IAM only
   its own stack's roles (`<stack>-*`). Use `CreateOidcProvider=false` if the
   account already has GitHub's OIDC provider (the second environment in an
   account does):

   ```bash
   prefix=$(gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix)
   aws cloudformation deploy --template-file .github/deploy-role.yaml \
     --stack-name saas-dev-github-deploy --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides GitHubSubjectPrefix="$prefix" Environment=dev StackName=saas-dev
   ```

   The prefix is what GitHub puts in its tokens. Newer repositories use
   immutable ids (`repo:owner@123/name@456`), so a renamed or recreated
   repository can't take the role over.

2. Create the GitHub environment (Settings > Environments, or the API) and
   give it a variable `AWS_ROLE_ARN` with the stack's `RoleArn` output:

   ```bash
   gh api -X PUT repos/<owner>/<repo>/environments/dev
   gh variable set AWS_ROLE_ARN --env dev --body <RoleArn>
   ```

Before the first prod run, do what "Before production" lists: at least create
the SSM webhook secret and check prod's values in `samconfig.toml`.

## Operations

- **Logs:** all functions log JSON (Powertools) into one log group per stack,
  with the request id (`correlation_id`, the access log's `requestId`),
  `tenant_id` and `user_id` on every API log line. Routes log nothing when
  they work. An uncaught error is one `Unhandled error` line with its
  traceback (`Webhook failed` for the payment webhook), so a 500 in the access
  log leads to it by `correlation_id`; a message a worker or the relay
  couldn't process is a WARNING with its traceback. API Gateway access logs go
  to their own group. Retention: `LogRetentionDays`.
- **Tracing:** X-Ray tracing is on for every function. (The X-Ray SDK is in
  maintenance mode; for spans inside your code, use OpenTelemetry/ADOT.)
- **Metrics:** the relay emits `EventsPublished` to the `saas-<stage>` namespace.
- **Alarms** and budget alerts go to `AlertsTopic`; set `AlertEmail` to get an email.
  - `ApiErrorsAlarm`: the API answered 5xx. Start from the access log's
    `error` field, then `Unhandled error` in the app log group.
  - `AccountsPreTokenErrorsAlarm`: the login trigger errored, so sign-ins
    fail (Cognito doesn't retry it). A broken shared layer shows here first.
  - `OutboxLagAlarm`: events have waited more than 5 minutes to be published.
    The relay keeps retrying; see `outbox-relay` in the app log group.
  - Messages in any dead-letter queue (accounts, notifications, outbox, the
    assistant's purge). For accounts, notifications and the purge's
    `MemberRemoved` these are events the
    worker failed 5 times, or that EventBridge couldn't deliver to the queue
    within an hour (their `ERROR_CODE` message attribute says why): fix the
    cause and move the messages back with SQS "Start DLQ redrive". The outbox
    queue only says which stream records the relay could not publish within
    24 hours; republish those events from the table (they are kept 7 days):
    `uv run python scripts/replay-outbox.py --table <table> --bus <EventBusName> --since <time>`.
- **Busy items:** transactions that collide on the same item (for example many
  orders at once in one workspace) are retried; if they keep colliding, the
  API answers 503 and the client should retry.
- **Rate limits:** the HTTP API allows 50 requests/second (bursts of 100) by
  default; change `DefaultRouteSettings` in `template.yaml`.
- **Workers:** the accounts and notifications workers run at most 5 at a time
  (`MaximumConcurrency`), the assistant's purge 2. A backlog or a DLQ redrive
  waits in its queue instead of using up the account's Lambda concurrency,
  which the API and the login trigger need.
- **Cost:** every resource carries the stack's `project` and `stage` tags
  (`tags` in `samconfig.toml`). Activate them once per account (Billing >
  Cost allocation tags), and Cost Explorer splits the bill by project and
  stage from then on. `AccountBudgetUsd` sets a monthly budget for the whole
  account; it alerts at 80% and when the month's forecast passes it. OpenRouter
  bills you directly, so give its key a credit limit there.

## Adding a module

1. Copy `modules/orders` as a starting point and rename the package (folder,
   `src/<name>/`, and `name` / `module-name` in its `pyproject.toml`).
2. Give it its own settings, models, schemas, table, container and tests.
3. Run `uv sync --all-packages` and `./scripts/export-requirements.sh`
   (the workspace picks up `modules/*` automatically).
4. Add a section to `template.yaml` (table + functions + routes).
5. If it publishes events: enable a stream and `expires_at` TTL on its table
   and add the stream to `OutboxRelayFunction`. If it listens: add a rule, a
   queue with a dead-letter queue and an alarm, like accounts does, and give
   the rule's target that dead-letter queue (`RetryPolicy`, `DeadLetterConfig`
   and a queue policy that lets the rule write to it, as `AccountsDLQPolicy`).
6. Talk to other modules only through events or the token.

## The sample module

`modules/orders` is a small domain module kept as a worked example: its own
table and routes, a plan limit read from the token (`max_orders`), a counter
kept by a DynamoDB condition, allowed status changes, cursor paging, the
`OrderCreated` event through the outbox, the notification it causes, and the
web pages that use it. Copy it to start your first module ("Adding a
module"), then remove it. Outside its own folders, this is what uses it:

| Where | What to change |
|---|---|
| `modules/orders/` | Delete it, then run `uv lock` and `./scripts/export-requirements.sh` |
| `template.yaml` | The "Module: orders" section, the `Orders` stream of `OutboxRelayFunction`, `OrderCreated` in `NotificationsRule`, and the stack's `Description` |
| `modules/notifications/` | `_on_order_created` and its `HANDLERS` entry in `service.py`; the two tests in `test_notifications.py` that use `OrderCreated`; the docstring in `__init__.py` |
| `layers/shared/layer/shared/plans.py` | `max_orders`: rename it to your module's limit, or remove it here, in `web/src/shared/lib/plans.ts` and on the billing page |
| `scripts/smoke-test.py` | The auth checks, the Orders section, and the order checks in the isolation and roles sections: point them at your module's routes |
| `.env.example` | `ORDERS_TABLE` |
| `web/src/features/orders/`, `web/src/routes/_app/orders/` | Delete them; `pnpm gen:api` then removes the generated types |
| `web/src/features/shell/app-shell.tsx` | The Orders menu item and the `PackageIcon` import |
| `web/src/features/overview/overview-page.tsx` | The order-limit card and the recent-orders card |
| `web/src/shared/api/types.ts`, `keys.ts` | The `Order*` types and the order query keys |
| `web/src/routes.test.tsx` | The orders page cases, and the two guard tests that use `/orders` as their example path |
| `web/src/shared/i18n/locales/*.json` | `orders`, `nav.orders`, and the order keys under `overview` and `billing.features` |

The type check and the tests point at most of what is left; check the smoke
test, the locale files and the query keys by hand. This README's examples
(dependency injection, events, flows) use orders too.

## Moving a module to its own repository

Copy `modules/<name>/`, move its section of `template.yaml`, and give it its
own HTTP API (or put several APIs behind a custom domain with path mappings).
No code changes are needed, because it doesn't import other modules.

Point its dependencies at a tagged version of this repository instead of the
workspace, in the module's `pyproject.toml`:

```toml
[tool.uv.sources]
saas-shared = { git = "https://github.com/<you>/saas-backend-v2", subdirectory = "layers/shared", tag = "shared-v0.1.0" }
saas-testkit = { git = "https://github.com/<you>/saas-backend-v2", subdirectory = "testkit", tag = "shared-v0.1.0" }
```

Its template still needs the shared layer and, if it publishes events, an
outbox relay function and an event bus: either build them from `saas-shared`
the same way this template does, or list `saas-shared` in its requirements so
it is bundled into each function. Copy `conftest.py` (the fake AWS fixture) too.

## Before production

- Check prod's values in `samconfig.toml`. `AppUrl` and `CorsOrigin` are
  empty, so the app runs at its CloudFront address (`WebUrl`); for your own
  domain, add it and its certificate to `WebDistribution` and set both to it.
  Set `AlertEmail` to your address for alarms and budget alerts, and confirm
  its subscription email after the first deploy. Set `AccountBudgetUsd` to
  what you expect the account to spend in a month (dev's spending counts too
  while both share an account), and activate the cost allocation tags
  ("Operations")
- To send email, verify your domain in SES, request SES production access,
  and set `EmailSender` and `SesIdentity`. Until then app emails, invitations
  included, are only logged
- Switch prod's Stripe to live mode ("Payments"), and turn on Stripe Tax if
  you need to charge tax
- Review the rate limits and the invitation rules (`INVITATION_DAYS`,
  `INVITES_PER_DAY`, `MAX_PENDING_INVITATIONS_PER_EMAIL`)
- Dev and prod share one AWS account by default. A separate prod account is
  a stronger boundary: in a shared account the dev deploy role, which any
  branch can use, could also change prod's resources directly. If you move
  prod, create its deploy role (with `CreateOidcProvider=true`) and its SSM
  secret in that account
