# saas-backend-v2: instructions for agents

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

- Backend: `modules/<name>/src/<name>/` holds `api.py` (routes), `service.py`, `repository.py`
  (DynamoDB), `models.py` and `schemas.py` (HTTP shapes), with tests in `modules/<name>/tests/`.
  Code shared by modules (auth, HTTP, events, plans) is in `layers/shared/layer/shared/`.
- Web: `web/src/features/<feature>/`, routes in `web/src/routes/`, the API client in
  `web/src/shared/api/`, UI text in `web/src/shared/i18n/locales/en.json` and `vi.json`.
- Infra: `template.yaml` (SAM). Stacks: `saas-dev` and `saas-prod` (`samconfig.toml`).

## Conventions

- The caller and their role come from the ID token (`get_current_user` in
  `layers/shared/layer/shared/auth.py`), never from the request. A request names its workspace
  (the `X-Tenant-Id` header), and only a workspace the token lists is accepted.
- A workspace's items are keyed under `TENANT#<tenant_id>`, and one user's under
  `TENANT#<tenant_id>#USER#<user_id>`. The other keys (`USER#<id>` memberships, the `EMAIL#`
  index, `OUTBOX#`, `IDEMPOTENCY#`) are lookups and system records.
- Each `repository.py` starts with its key design; update it when keys change.
- Rules that must hold under concurrent requests (limits, seats, memberships) are DynamoDB
  conditions, not read-then-write code.
- After changing an API route or schema, run `cd web && pnpm gen:api` and commit the generated
  files.
- Every UI string goes in both `en.json` and `vi.json`.
- When stored items change shape, say how existing items are read or backfilled. For the
  assistant, check dev with `uv run python scripts/check-assistant-data.py --config-env dev`.
- Docs change with the code: `README.md` and `modules/<name>/README.md` describe current behavior,
  commands and outputs.
- A commit message is one plain sentence in the imperative saying what changes for the user, with
  no prefix: "Keep the old version when a message is edited or answered again".

## The loop

Every change takes the same path, with Slipway, the ship kit
(`uv tool install --editable ~/Developer/tools/ship-kit` puts its `ship` command on PATH).
`ship` reads [`.ship/profile.yaml`](.ship/profile.yaml): the plans folder, the gates (the checks
above), the builder's model, this repo's security facts, the live check on `saas-dev`, and the
owner's project page and work log.

The loop's skills come from the kit's `slipway` plugin, which this repo enables in
`.claude/settings.json`. Install it once, in this repo, with
`claude plugin install slipway@slipway --scope project`.
- `/slipway:ship` finds the stage.
- `/slipway:ship-plan`, `/slipway:ship-build`, `/slipway:ship-review` and `/slipway:ship-land`
  run the parts. Plans live in `plans/`, deferred work in `plans/BACKLOG.md`.
- A build hands each part to the `ship-builder` agent on Sonnet and commits it itself; a change to
  a sensitive path is reviewed against the profile's security facts first; a live check deploys
  the branch to `saas-dev` with `ship deploy-branch`, which refuses a branch behind `main`, a
  deploy in flight, and taking dev from another branch.

The plugin's hooks hold rules that broke before. A refusal names the rule and the way through.
They refuse:
- a commit on `main`, on a branch this session holds no lease on (`ship lease acquire --task
  <id>` in the worktree), or with a tool attribution;
- `gh pr merge`: the owner merges (`SHIP_ALLOW=merge` asks the owner);
- a change to a frozen plan outside its Status paragraph.

Production deploys are the owner's: `ship deploy-branch` deploys to `saas-dev` only.
