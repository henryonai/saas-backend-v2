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
