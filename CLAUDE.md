# saas-backend-v2

## Build & test

The same checks CI runs (see README.md for deploys):

```bash
uv run pytest                        # backend tests (uv run pytest modules/<name> for one module)
./scripts/export-requirements.sh --check   # requirements.txt files match uv.lock
uvx ruff check .                     # backend lint
uvx cfn-lint template.yaml .github/deploy-role.yaml
cd web && pnpm gen:api && pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

`pnpm gen:api` must leave `web/src/shared/api/generated` unchanged; CI fails if it doesn't.

After changing backend dependencies, run `./scripts/export-requirements.sh` before `sam build`.

## Layout

- Backend: `modules/<name>/src/<name>/` holds `api.py` (routes), `service.py`, `repository.py` (DynamoDB), `models.py` and `schemas.py` (HTTP shapes), with tests in `modules/<name>/tests/`. Code shared by modules (auth, HTTP, events) is in `layers/shared/layer/shared/`.
- Web: `web/src/features/<feature>/`, routes in `web/src/routes/`, the API client in `web/src/shared/api/`, UI text in `web/src/shared/i18n/locales/en.json` and `vi.json`.
- Infra: `template.yaml` (SAM). Stacks: `saas-dev` and `saas-prod` (`samconfig.toml`).

## Conventions

- The caller's tenant, user and role come from the ID token (`get_current_user` in `layers/shared/layer/shared/auth.py`), never from the request. A tenant's items are keyed under `TENANT#<tenant_id>`, and one user's items under `TENANT#<tenant_id>#USER#<user_id>`. The other keys (`USER#<id>` membership, the `EMAIL#` index, `OUTBOX#`, `IDEMPOTENCY#`) are lookups and system records.
- Each `repository.py` starts with its key design; update it when keys change.
- Rules that must hold under concurrent requests (limits, one workspace per user) are DynamoDB conditions, not read-then-write code.
- After changing an API route or schema, run `cd web && pnpm gen:api` and commit the generated files.
- Every UI string goes in both `en.json` and `vi.json`.
- When stored items change shape, say how existing items are read or backfilled. For the assistant, check dev with `uv run python scripts/check-assistant-data.py --config-env dev`.
- Docs change with the code: `README.md` and `modules/<name>/README.md` describe current behavior, commands and outputs.
- A commit message is one plain sentence in the imperative saying what changes for the user, with no prefix: "Keep the old version when a message is edited or answered again".

## Slipway

The feature flow, as skills in `.claude/skills/` and agents in `.claude/agents/`:
`/brainstorm` → `/build-feature` → `/deploy-dev` → `/e2e` → `/ship`.

- Specs and plans are saved in `.slipway/specs/` (gitignored).
- Notion project: [SaaS backend on AWS (saas-backend-v2)](https://app.notion.com/p/404c6f66e650814e83e7de2a824ae0f9).
- Security-sensitive, so a change here also gets `security-reviewer`: `layers/shared/`, `modules/accounts/`, `modules/billing/`, keys or queries in any `repository.py`, IAM and resources in `template.yaml`, and in `modules/assistant/` its tools, sandbox, file handling, memory, middleware and agent prompts (`agents/*.md`).
- Slipway deploys and tests only on `saas-dev`. Prod deploys are the user's.
