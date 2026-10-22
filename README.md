# SaaS backend (multi-tenant, serverless)

Python on AWS Lambda, with Lambda Powertools, DynamoDB, Cognito and EventBridge.
Four self-contained modules: **accounts, orders, notifications, billing**.

## Layout

```
saas-backend/
├── pyproject.toml              # uv workspace root: dev tools + pytest config
├── uv.lock                     # exact versions of every package (commit it)
├── .python-version             # 3.13, same as the Lambda runtime
├── scripts/
│   └── export-requirements.sh  # uv.lock -> requirements.txt files for sam build
├── layers/shared/              # package "saas-shared"
│   ├── pyproject.toml          # powertools, pydantic, pydantic-settings
│   ├── layer/                  # deployed as a Lambda layer
│   │   ├── requirements.txt    # generated, do not edit
│   │   └── shared/             # plumbing only, no business logic
│   │       ├── auth.py         # CurrentUser + get_current_user (Depends)
│   │       ├── errors.py       # NotFound, Forbidden, PlanLimitReached ...
│   │       ├── events.py       # EventPublisher interface + EventBridge version
│   │       ├── http.py         # create_app / create_handler
│   │       ├── plans.py        # plan catalog and limits
│   │       └── settings.py     # BaseAppSettings (pydantic-settings)
│   └── tests/
├── modules/
│   └── <module>/               # package "saas-<module>", close to a ready-made repository
│       ├── pyproject.toml      # depends on saas-shared; add module-only packages here
│       ├── src/requirements.txt  # generated, do not edit
│       ├── src/<module>/
│       │   ├── settings.py     # this module's config (env vars)
│       │   ├── models.py       # Pydantic models
│       │   ├── repository.py   # the only code touching this module's table
│       │   ├── service.py      # business rules; all dependencies passed in
│       │   ├── container.py    # builds the service once (composition root)
│       │   ├── api.py          # HTTP routes, using Depends()
│       │   ├── handler.py      # Lambda entry point for the HTTP API
│       │   └── ...             # other entry points (worker, webhook, triggers)
│       └── tests/
├── testkit/                    # package "saas-testkit": test helpers and fakes (not deployed)
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
6. **`tenant_id` comes from the login token, never from the request.**

## How modules talk to each other

| Need | Pattern | Example |
|---|---|---|
| Facts about the caller | Login token | Orders reads `plan` from the token to check its limit |
| Something happened | Event | Orders publishes `OrderCreated`; notifications reacts |
| Listener needs details | Fat event | Billing's events include `owner_email`, so notifications can email without asking accounts |
| Need another module's data often | Local copy via events | Accounts keeps a copy of the plan, updated by `SubscriptionChanged` |

**Events**

| Event | Published by | Used by |
|---|---|---|
| `UserInvited` | accounts | notifications (invite email) |
| `OrderCreated` | orders | notifications (in-app) |
| `SubscriptionChanged` | billing | accounts (plan copy), notifications |
| `PaymentFailed` | billing | notifications |

## Main flows

**Sign-up:** user signs up in Cognito → `POST /tenants` creates the workspace and
makes them `owner` → frontend refreshes the token → the login trigger adds
`tenant_id`, `role` and `plan`.

**Upgrade:** owner calls `POST /billing/checkout` → pays on the provider's page →
provider calls `/webhooks/payments` → signature checked, duplicates skipped →
billing publishes `SubscriptionChanged` → accounts updates its plan copy and
notifications tells the owner → after the next token refresh, the token
carries the new plan. The frontend should refresh the token after checkout.

## Dependency injection, in one example

```python
# container.py: built once per Lambda instance
@lru_cache
def get_order_service() -> OrderService:
    s = get_settings()
    return OrderService(orders=OrderRepository(table(s.orders_table)),
                        publisher=EventBridgePublisher(client("events"), s.event_bus_name))

# api.py: the route declares what it needs
@router.post("/orders")
def create_order(body: OrderCreate,
                 user: Annotated[CurrentUser, Depends(get_current_user)],
                 service: Annotated[OrderService, Depends(get_order_service)]): ...

# tests: swap it, no monkeypatching
app.dependency_overrides[get_order_service] = lambda: OrderService(fake_repo, FakePublisher())
```

## Configuration and secrets

- Settings classes (`settings.py`) read environment variables set in `template.yaml`.
  A missing value fails at cold start with a clear message.
- **Secrets are not environment variables.** The payment webhook secret lives
  in SSM Parameter Store; only its *name* is in the environment. Create it once
  per environment before deploying:

  ```bash
  aws ssm put-parameter --name /saas/dev/payment-webhook-secret \
      --type SecureString --value "$(openssl rand -hex 32)"
  ```
- For local runs, copy `.env.example` to `.env` (never commit `.env`).
- Dev and prod run the same code with different values (`samconfig.toml`).

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
`uv.lock`. boto3 is not bundled because the Lambda runtime provides it.

## Tests (offline)

```bash
uv sync --all-packages
uv run pytest                   # everything
uv run pytest modules/orders    # one module
```

## Deploy

```bash
./scripts/export-requirements.sh
sam build
sam deploy --config-env dev
sam sync --config-env dev   # fast code sync while developing
sam deploy --config-env prod
```

Outputs: `ApiUrl`, `UserPoolId`, `UserPoolClientId` (for the React app) and
`WebhookUrl` (for the payment provider). The frontend sends the **ID token**
in the `Authorization` header.

## Adding a module

1. Copy `modules/orders` as a starting point and rename the package (folder,
   `src/<name>/`, and `name` / `module-name` in its `pyproject.toml`).
2. Give it its own settings, table, container and tests.
3. Run `uv sync --all-packages` and `./scripts/export-requirements.sh`
   (the workspace picks up `modules/*` automatically).
4. Add a section to `template.yaml` (table + functions + routes).
5. Talk to other modules only through events or the token.

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

Its template still needs the shared layer: either build one from `saas-shared`
the same way this template does, or list `saas-shared` in its requirements so
it is bundled into each function. Copy `conftest.py` (the fake AWS fixture) too.

## Before production

- Set `CorsOrigin` and `AppUrl` for prod in `samconfig.toml`
- Verify your SES domain and request production access
- Replace `DevPaymentProvider` in `billing/container.py` with a real provider
- Set CloudWatch log retention (logs are kept forever by default)
- Consider disabling `/swagger` in prod
