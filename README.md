# SaaS backend (multi-tenant, serverless)

Python on AWS Lambda, with Lambda Powertools, DynamoDB, Cognito and EventBridge.
Four self-contained modules: **accounts, orders, notifications, billing**.

## Layout

```
saas-backend/
├── layers/shared/
│   ├── layer/                  # deployed as a Lambda layer
│   │   ├── requirements.txt    # powertools, pydantic, pydantic-settings
│   │   └── shared/             # plumbing only, no business logic
│   │       ├── auth.py         # CurrentUser + get_current_user (Depends)
│   │       ├── errors.py       # NotFound, Forbidden, PlanLimitReached ...
│   │       ├── events.py       # EventPublisher interface + EventBridge version
│   │       ├── http.py         # create_app / create_handler
│   │       ├── plans.py        # plan catalog and limits
│   │       └── settings.py     # BaseAppSettings (pydantic-settings)
│   └── tests/
├── modules/
│   └── <module>/               # each folder is close to a ready-made repository
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
├── testkit/                    # test helpers and fakes (not deployed)
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

## Tests (offline)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                      # everything
pytest modules/orders       # one module
```

## Deploy

```bash
sam build
sam deploy --config-env dev
sam sync --config-env dev   # fast code sync while developing
sam deploy --config-env prod
```

Outputs: `ApiUrl`, `UserPoolId`, `UserPoolClientId` (for the React app) and
`WebhookUrl` (for the payment provider). The frontend sends the **ID token**
in the `Authorization` header.

## Adding a module

1. Copy `modules/orders` as a starting point and rename the package.
2. Give it its own settings, table, container and tests.
3. Add its path to `pytest.ini`.
4. Add a section to `template.yaml` (table + functions + routes).
5. Talk to other modules only through events or the token.

## Moving a module to its own repository

Copy `modules/<name>/`, `layers/shared/` and `testkit/`, move its section of
`template.yaml`, and give it its own HTTP API (or put several APIs behind a
custom domain with path mappings). No code changes are needed, because it
doesn't import other modules.

## Before production

- Set `CorsOrigin` and `AppUrl` for prod in `samconfig.toml`
- Verify your SES domain and request production access
- Replace `DevPaymentProvider` in `billing/container.py` with a real provider
- Set CloudWatch log retention (logs are kept forever by default)
- Consider disabling `/swagger` in prod
