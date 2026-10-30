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

   The template is MIT-licensed (`LICENSE`): keep that notice in your copy.
   Your own changes can carry your name, or a licence of your own, beside it.
3. Deploy dev. `sam build` installs dependencies for the Lambda runtime, so it
   needs a Python 3.13 that has pip on `PATH` (uv's own, for example:
   `export PATH="$(dirname "$(uv python find --system 3.13)"):$PATH"`; the
   project's `.venv` has no pip), or `sam build --use-container`. The stack
   and SSM names below are the project's own: in a copy, the rename script has
   already changed them.

   ```bash
   read -rs STRIPE_KEY && aws ssm put-parameter --name /saas/dev/stripe-secret-key \
       --type SecureString --value "$STRIPE_KEY"; unset STRIPE_KEY   # a test key
   ./scripts/export-requirements.sh && sam build
   sam deploy --config-env dev        # the first deploy in an account: no assistant yet
   uv run python scripts/stripe-setup.py --stack saas-dev
   (cd web && ./scripts/deploy.sh saas-dev)   # the app, at the stack's WebUrl
   uv run scripts/smoke-test.py --stack saas-dev
   ```

   For the assistant, put an OpenRouter key in SSM the same way, deploy again
   with its code (its zip goes to the bucket the first `sam deploy` created),
   and publish the app again, so it gets the new `AssistantUrl`:

   ```bash
   read -rs OPENROUTER_KEY && aws ssm put-parameter --name /saas/dev/openrouter-api-key \
       --type SecureString --value "$OPENROUTER_KEY"; unset OPENROUTER_KEY
   sam deploy --config-env dev \
     --parameter-overrides "$(uv run python scripts/package-assistant.py --config-env dev)"
   (cd web && ./scripts/deploy.sh saas-dev)
   uv run scripts/smoke-assistant.py --stack saas-dev
   ```

   Then set up the deploy workflow ("CI/CD").
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
│   ├── replay-outbox.py        # publish saved events again after an outage
│   ├── migrate-memberships.py  # once, for stacks from before users had several workspaces
│   ├── backfill-admin.py       # once, for stacks from before the admin module: its summaries
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
│   │       ├── exports.py      # an export's parts: where each module writes its own
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
│   (modules/audit only listens and lists: it keeps who did what in a
│    workspace, from the other modules' events, for GET /audit.
│    modules/admin is the back-office for platform admins: a read model of
│    every workspace, built from events, and the admin log.
│    modules/assistant's chat runs on AgentCore Runtime: app.py, with its
│    agents as Markdown files in src/assistant/agents/; its handler.py serves
│    the users' conversations on the HTTP API)
├── docs/
│   ├── data-model.md           # every table: keys, indexes, TTLs, streams, access patterns
│   └── openapi/                # generated: each module's OpenAPI document
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

**Where data lives:** [`docs/data-model.md`](docs/data-model.md) shows every
table on one page: keys, indexes, TTLs, streams and which query serves which
route. Each `repository.py` starts with its own key design; change both together.

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
| `MemberRemoved` | accounts | assistant (deletes the member's conversations, memories and files in that workspace), notifications (deletes their notifications there) |
| `TenantDeletionScheduled` | accounts (its owner deleted the workspace) | billing (the Stripe subscription stops renewing) |
| `TenantRestored` | accounts (its owner restored it) | billing (it renews again) |
| `TenantDeleted` (with `user_ids`, the members) | accounts' purge job | every module deletes what it keeps for the workspace: orders, notifications, the assistant's conversations, memories, files and usage; billing cancels the subscription |
| `UserDeleted` (`tenant_id` empty) | accounts | none yet: for a module that keeps data per user outside any workspace |
| `ExportRequested` (`job_id`, `kind`, `owner_id`, `tenant_ids`) | accounts | each module in the job's parts writes its part to the exports bucket: accounts, orders and billing for a workspace; accounts, notifications and the assistant for a user |
| `ExportReady` | accounts (once every part is in the bucket) | notifications (email, and in-app for a workspace's export) |
| `OrderCreated` | orders | notifications (in-app) |
| `TenantCreated`, `MemberJoined`, `RoleChanged`, `InvitationCancelled` (`declined`) | accounts | audit, admin (its summaries) |
| `TenantDeletionScheduled`, `TenantRestored` | accounts | audit, admin; orders, billing and the assistant (their copy of the workspace's status) |
| `PlanGranted` (`plan`, `note`) | admin | billing (gives the plan without payment, `comped`, until Stripe changes it), audit |
| `TenantSuspended` (`reason`), `TenantUnsuspended` | admin | accounts (a suspended workspace leaves every token), admin, audit; orders, billing and the assistant (their copy of the workspace's status) |

Every event about a workspace is also written to its **audit log** (the audit
module, `GET /audit` for owners and admins, an "Activity" tab on Team): who did
it (`actor_id` and `actor_email`, which accounts' events carry; `user_id` for
orders and exports; nobody for the payment provider's), and the event's other
fields. Entries are kept a year (`AUDIT_RETENTION_DAYS`), are part of a
workspace's export, and go when the workspace is purged. To log an event, add
it to `AUDITED` in `modules/audit/src/audit/service.py` and to `AuditRule`.
| `SubscriptionChanged` (with `revision`) | billing | accounts (plan copy), notifications |
| `PaymentFailed` | billing | notifications |

**How an event travels:** service creates an `Event` → repository saves it as
an `OUTBOX#` item in the same transaction as the change (kept 7 days) → the
table's DynamoDB stream triggers `OutboxRelayFunction` → it publishes to this
stack's event bus, retrying for up to an hour → a rule sends it to the
listener's SQS queue → the listener's worker (with a dead-letter queue, which
also gets what the rule couldn't deliver within an hour).
Delivery is at least once and not in order, hence rule 9.

## Main flows

**Sign-up:** user signs up in Cognito (verifies their email) → `POST /tenants`
creates the workspace and makes them `owner` → frontend refreshes the token →
the login trigger lists the workspace in the token's `workspaces` claim, with
their role there and its plan.

**Several workspaces:** a user can belong to up to 20 workspaces
(`MAX_WORKSPACES_PER_USER`): create more with `POST /tenants` (with an
`Idempotency-Key`, see Operations), or accept
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

**Delete a workspace:** its owner calls `DELETE /tenants/me` with
`{"name": "<its name>"}` (typed again to confirm) → the workspace is
`deleting`: every member's next token leaves it out (the owner's app refreshes
at once; others' tokens within 15 minutes), billing sets its Stripe subscription
to end with the period paid for instead of renewing (a restore undoes that; a
renewal the customer had stopped themselves stays stopped), `GET /tenants` still lists it with
its `status` and `delete_after`, and its owner can bring it back with
`POST /tenants/<tenant_id>/restore` until `TenantDeletionDays` (7; dev: 0.001, about 90 seconds)
have passed → the hourly purge job (`accounts/purge.py`) finds it in a sparse
index, publishes `TenantDeleted` with its members, and removes the
memberships and the workspace's own items → each module deletes its data (see
the events table); billing cancels the Stripe subscription now, without a
refund, and keeps a tombstone for 90 days so Stripe's late webhooks can't bring
it back. A purge that fails half way is finished by the next run.

**A suspended workspace, or one being deleted, takes no changes**, though
members' tokens list it for up to 15 more minutes: accounts checks the
workspace itself, and orders, billing and the assistant each keep a copy of its
status in their own table (`TENANT#<t>` / `STATUS`, from the events above,
seconds behind; `shared/workspace_status.py`). Changes get `403` ("This
workspace is suspended" / "is being deleted"): new orders and order status
changes, checkout, assistant messages, uploads and memories, invitations, role
changes, removing members and workspace exports. Reading still works, a member
can still leave, the billing portal stays open (to fix a payment or cancel),
and users can still delete their own conversations, memories and files. A stack
that had suspended or deleted workspaces before these copies existed writes
them once with `uv run scripts/backfill-workspace-status.py --stack <stack>`.

**Delete an account:** the user calls `DELETE /account` with
`{"email": "<their email>"}` (typed again to confirm) → refused while they own
an active workspace (ownership can't be handed over: they delete it first) →
they leave every other workspace as leaving does (`MemberRemoved`, so their
conversations, memories, files and notifications there go) → their own
records go and `UserDeleted` is published → their Cognito user is deleted,
last, so a failure part way can be retried. Orders they created stay with the
workspace (its records), with their user id.

**Back-office** (platform admins: the people who run the product): add
someone with `aws cognito-idp admin-add-user-to-group --user-pool-id
<UserPoolId> --username <their email> --group-name platform-admins`; their next
token's `cognito:groups` lists it, and the app shows "Admin" (`/admin`). The
admin module keeps a summary of every workspace (name, owner, plan, status,
members, orders) from events, never reading other modules' tables; each event
is applied once (a marker in the same transaction), and a late plan revision is
skipped. `GET /admin/tenants`, `GET /admin/tenants/<id>`, `GET /admin/log`; acting
publishes an event that the owning module applies, and logs it:
`POST /admin/tenants/<id>/plan` (`PlanGranted`: billing gives the plan without
payment until Stripe changes it), `.../suspend` with a reason and
`.../unsuspend` (accounts takes the workspace out of every token, as deleting
does; its data stays). A stack that had workspaces before the admin module
fills the summaries once with `uv run scripts/backfill-admin.py --stack <stack>`.

**Export data** (a job a user starts and watches): the owner calls
`POST /exports/workspace` (members, invitations, orders, subscription), or any
user `POST /exports/me` (their memberships, notifications, and assistant
conversations, memories and file list, in every workspace; never other
members' conversations) → accounts saves the job (`JOB#`, with the parts it
waits for) and `ExportRequested` in one transaction (at most 3 a day per
workspace, and per user) → each module writes its part to
`exports/<kind>/<owner>/<job>/<module>.json` in the exports bucket with its own
role → the bucket's "Object Created" goes through EventBridge (the default bus)
to accounts, which marks the part done → when all are, `ExportReady` makes
notifications tell the requester → `GET /exports/workspace` or `/exports/me`
shows the newest job and, once done, a 15-minute download link per part. The
bucket removes exports after 7 days, and TTL the job. Copy this for any long
job: the job record, one part per worker, and a fan-in that marks parts done.

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
- **Secrets are not environment variables.** Stripe's secret key and webhook
  signing secret live in SSM Parameter Store; only their *names* are in the
  environment, and only the functions that need them can read them (cached for
  5 minutes, so rotation needs no redeploy). "Payments" says how to put them there.
- **Email:** set `EmailSender` and `SesIdentity` (the domain or address
  verified in SES) to send app emails *and* Cognito's sign-up codes through
  SES. Without them, app emails are only logged and Cognito uses its built-in
  sender, which allows about 50 emails a day.
- For local runs, copy `.env.example` to `.env` (never commit `.env`). Tests
  ignore it.
- Dev and prod run the same code with different values (`samconfig.toml`).
  Each stack has its own resources, names, event bus and secret, so both
  could share one AWS account, but this project keeps each in its own account
  ("Before production" says why), with the CLI profiles `saas-dev` and
  `saas-prod`: set `AWS_PROFILE` before a local `sam` command or script, which
  otherwise go to whichever account your default credentials belong to.

## Payments

Billing takes payments with Stripe, on every stack: dev in Stripe's test mode,
prod in test mode until you launch. Checkout is Stripe Checkout; the card,
invoices, plan changes and cancelling are in Stripe's customer portal
("Manage billing"); Stripe's signed webhooks change the plan. Billing talks to
Stripe through one interface (`billing/provider.py`), so another provider is
one class (`billing/stripe_provider.py` is the example). Unit tests use a fake
of Stripe's API, with Stripe's own signature checks.

Setting up Stripe for a stack:

1. Put the Stripe secret key in SSM yourself, so it is never shown or stored
   anywhere else (a test key `sk_test_...`, or a restricted key):

   ```bash
   read -rs STRIPE_KEY && aws ssm put-parameter --name /saas/dev/stripe-secret-key \
       --type SecureString --value "$STRIPE_KEY"; unset STRIPE_KEY
   ```

2. Deploy the stack.
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

In prod, a deploy moves the API functions (the `live` alias of each, `Mappings.Rollout`
in `template.yaml`) to the new code gradually: CodeDeploy sends 10% of requests to it
for 5 minutes, then the rest. If `ApiFunctionErrorsAlarm` or `LambdaThrottlesAlarm`
fires meanwhile, it moves them back and the deploy fails and rolls back, so a prod
deploy takes about 5 minutes longer. The workers and the relay switch at once: their
queues and streams retry what fails. Dev switches everything at once.

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
publishes the app), `WebApiUrl` (the API on the app's address, which the
deployed app calls), and `AssistantUrl` when the assistant is deployed.

**The API behind CloudFront:** the app's CloudFront distribution sends `/api/*`
to the HTTP API (`/api/orders` → `/orders`, a CloudFront Function), with nothing
cached and every header but `Host`, so the app and its API share one address
and need no CORS. App routes (`/orders/123`) get `index.html` from another
CloudFront Function, so the API's own 403 and 404 reach the app unchanged.
The HTTP API's own address (`ApiUrl`) still works, protected as before by the
JWT check and its throttle: scripts, the assistant's tools and Stripe's webhook
(`WebhookUrl`) use it. Behind CloudFront, the access log's `ip` is CloudFront's.
A WAF can go on the distribution later to rate-limit at the edge; it isn't there
now, to keep the cost at CloudFront's (free up to 1 TB and 10 million requests a month).

The tables and the user pool are kept when the stack is deleted (and can't be
deleted at all in prod), so data survives mistakes. To remove a dev stack
completely (checked on a fresh copy's stack):

1. Empty the web and exports buckets (`WebBucketName`, and the stack's
   `ExportsBucket`): CloudFormation refuses to delete a bucket with objects in
   it, and the stack ends in `DELETE_FAILED`.
2. `aws cloudformation delete-stack --stack-name saas-dev`. With the assistant,
   the delete can fail on `AssistantSandbox` with "already exists": delete the
   code interpreter yourself
   (`aws bedrock-agentcore-control delete-code-interpreter --code-interpreter-id <its id>`,
   the id in the error) and delete the stack again.
3. Delete what the stack keeps: its 6 tables (8 with the assistant), the user
   pool, the assistant's files and checkpoint buckets (empty them first), the
   assistant runtime's log group (`/aws/bedrock-agentcore/runtimes/saas_dev_assistant-…`),
   and the SSM parameters under `/saas/dev/`.
4. In Stripe's test mode, `stripe-setup.py`'s webhook can be deleted; its
   products, prices and portal configuration can only be archived.

`sam deploy` also keeps its own stack, `aws-sam-cli-managed-default`, with a
versioned bucket that every SAM deploy in that account and region shares:
delete it only when nothing else there uses SAM (empty every version first).

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
CI fails if they are out of date. Checkout is Stripe's test mode on dev (test
card `4242 4242 4242 4242`). See [web/README.md](web/README.md).

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

Before the first prod run, do what "Before production" lists: at least put
the Stripe key in SSM ("Payments") and check prod's values in `samconfig.toml`.
After its first deploy, run `stripe-setup.py --stack <prod stack>`.

## Operations

- **Logs:** all functions log JSON (Powertools) into one log group per stack,
  with the request id (`correlation_id`, the access log's `requestId`),
  `tenant_id` and `user_id` on every API log line. Routes themselves log
  nothing when they work. An uncaught error is one `Unhandled error` line with its
  traceback (`Webhook failed` for the payment webhook), so a 500 in the access
  log leads to it by `correlation_id`; a message a worker or the relay
  couldn't process is a WARNING with its traceback. Each API request that
  returns writes one `Request` line (route, status, `ms`), so Logs Insights on
  the app log group can count requests per workspace:
  `filter message = "Request" | stats count(*) by tenant_id | sort count(*) desc`
  (HTTP APIs can't log a request header in their access log). API Gateway
  access logs go to their own group. Retention: `LogRetentionDays`.
- **Tracing:** X-Ray tracing is on for every function. (The X-Ray SDK is in
  maintenance mode; for spans inside your code, use OpenTelemetry/ADOT.)
- **Metrics:** the relay emits `EventsPublished` to the `saas-<stage>` namespace.
- **Dashboard:** one CloudWatch dashboard per stack (output `DashboardUrl`): the
  alarms' state, API requests, errors and latency, errors per function, the
  account's Lambda concurrency and throttles, messages waiting in each queue
  and dead-letter queue, events published and how long the oldest waited, and
  the assistant when it is deployed.
- **Alarms** and budget alerts go to `AlertsTopic`; set `AlertEmail` to get an email.
  - `ApiErrorsAlarm`: the API answered 5xx. Start from the access log's
    `error` field, then `Unhandled error` in the app log group.
  - `AccountsPreTokenErrorsAlarm`: the login trigger errored, so sign-ins
    fail (Cognito doesn't retry it). A broken shared layer shows here first.
  - `OutboxLagAlarm`: events have waited more than 5 minutes to be published.
    The relay keeps retrying for up to an hour; see `outbox-relay` in the app log group.
  - `LambdaThrottlesAlarm`: Lambda refused to run an API, sign-in or relay
    function because the account's concurrency (shared by every stack in it) ran out.
  - `ApiDurationAlarm`, `WorkersDurationAlarm`: a function's p99 duration has
    been above 80% of its timeout for 15 minutes. The alarm's graph shows p99 /
    timeout per function; when you change a `Timeout`, change it in the alarm too.
  - `ApiFunctionErrorsAlarm` sends no email (`ApiErrorsAlarm` does): it is what
    rolls a prod deploy back.
  - Messages in any dead-letter queue (accounts, orders, billing,
    notifications, outbox, the purge job, the assistant's purge). For
    accounts, orders, billing, notifications and the assistant purge's
    `MemberRemoved` and `TenantDeleted` these are events the
    worker failed 5 times, or that EventBridge couldn't deliver to the queue
    within an hour (their `ERROR_CODE` message attribute says why): fix the
    cause and move the messages back with SQS "Start DLQ redrive". The outbox
    queue only says which stream records the relay could not publish within
    an hour; republish those events from the table (they are kept 7 days):
    `uv run python scripts/replay-outbox.py --table <table> --bus <EventBusName> --since <time>`.
  - `ListenerQueuesAgeAlarm`: a message has waited more than 15 minutes in a
    listener's queue, so a worker is failing slowly, throttled or capped by
    `MaximumConcurrency`. The dashboard's queue widget shows which one is backing up.
  - `LostEventsAlarm`: EventBridge could put events neither in a listener's
    queue nor in its dead-letter queue (a broken queue policy, say), so they
    are gone. Fix the cause, then republish them from the outbox with
    `replay-outbox.py` (above).
  - `AssistantPurgeLagAlarm`: expired assistant conversations have waited more
    than an hour to be purged.
- **Scheduled jobs:** EventBridge Scheduler (`ScheduleV2` in `template.yaml`)
  invokes the job's function asynchronously; Lambda retries a failed run
  twice, and a run that still fails, or a schedule that couldn't start one,
  lands in the job's dead-letter queue, which has an alarm. A job starts from
  what is stored, so the next run continues where a failed one stopped, and it
  stops before its timeout. `AccountsPurgeFunction` (hourly, purges deleted
  workspaces) is the example to copy; run one now with
  `aws lambda invoke --function-name <its name> out.json`.
- **Busy items:** transactions that collide on the same item (for example many
  orders at once in one workspace) are retried; if they keep colliding, the
  API answers 503 and the client should retry.
- **Retried creates:** `POST /orders` and `POST /tenants` require an
  `Idempotency-Key` header (a new UUID per order or workspace; without one,
  422). Sent again with the same request (after a timeout, a 503 or a double
  click), it returns what the first request made instead of making and
  counting another; the same key with a different request gets 400. The key
  is saved in the create's own transaction and kept 24 hours
  (`shared/idempotency.py`). The web app's new order dialog and create
  workspace form send one.
  Use the same helper for any create where the server picks the new id; a write
  with a natural key, or a condition on the current state, is safe already.
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
- **Cost anomalies:** AWS turns on Cost Anomaly Detection in new accounts
  (`Default-Services-Monitor`, a daily email to the account's address). The
  template doesn't add a monitor, since an account can have only one per-service
  monitor. Check its threshold under Billing > Cost Anomaly Detection: the
  default alerts only on anomalies of $100 or more, too high for a small app.
- **Dependency updates:** each week Dependabot opens one PR of minor and patch
  updates per ecosystem (uv, pnpm, GitHub Actions; `.github/dependabot.yml`),
  and a PR of its own for each major update, all checked by CI. New versions wait
  7 days first; security updates don't. TypeScript's major updates are skipped
  until the API generator supports TypeScript 7.
  `.github/workflows/dependabot.yml` commits the regenerated `requirements.txt` files
  to Python PRs, and merges each PR once CI passes, except major updates of production
  dependencies and of GitHub Actions, which stay open for you. Merging deploys nothing.
  Merges made this way start no CI run on `main`, so CI also runs there daily.
  This needs **Allow auto-merge** on (Settings > General) and CI's `check` and `web`
  as required status checks on `main`; without required checks, nothing merges.

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
   Add the queue to `ListenerQueuesAgeAlarm` and the rule to `LostEventsAlarm`.
   `tests/test_template.py` checks all of this for every rule, queue and
   stream reader in `template.yaml`, and names what is missing.
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
  empty, so the app runs at its CloudFront address (`WebUrl`) and calls its API
  there (`/api`), with no CORS; for your own domain, add it and its certificate
  to `WebDistribution` and set `AppUrl` to it.
  Set `AlertEmail` to your address for alarms and budget alerts, and confirm
  its subscription email after the first deploy (in a public repository, leave
  it empty and subscribe your addresses to the stack's `AlertsTopic` by hand,
  so they stay out of git). Set `AccountBudgetUsd` to
  what you expect prod's account to spend in a month (the budget covers the
  whole account it is deployed to), and activate the cost allocation tags
  in that account ("Operations")
- To send email, verify your domain in SES, request SES production access,
  and set `EmailSender` and `SesIdentity`. Until then app emails, invitations
  included, are only logged
- Switch prod's Stripe to live mode ("Payments"), and turn on Stripe Tax if
  you need to charge tax
- Review the rate limits and the invitation rules (`INVITATION_DAYS`,
  `INVITES_PER_DAY`, `MAX_PENDING_INVITATIONS_PER_EMAIL`)
- Deploy prod to its own AWS account, as this project does (dev in another).
  In a shared account the dev deploy role, which any branch can use, could
  also change prod's resources directly, and the two would share Lambda
  concurrency and other quotas. In prod's account, create its deploy role
  (with `CreateOidcProvider=true`), the GitHub `prod` environment's
  `AWS_ROLE_ARN`, and its SSM parameters ("Payments"). One account for both
  still works if you accept that

## License

MIT (`LICENSE`). Copies keep the notice.
