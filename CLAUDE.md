# saas-backend-v2

## Build & test

The same checks CI runs (see README.md for deploys):

```bash
uv run ruff check .                  # backend lint
uv run pytest                        # backend tests (uv run pytest modules/<name> for one module)
cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

After changing backend dependencies, run `./scripts/export-requirements.sh` before `sam build`.

<!-- ruflo:begin -->
## Ruflo

Set up with `ruflo-setup`, pinned to ruflo@3.60.1. It adds hooks, a project
memory and an MCP server (`claude-flow`). Claude Code does the work; Ruflo
records and recalls.

- Before non-trivial work, recall what this repo already knows:
  `ruflo memory search --query "<topic>"`
- When a pattern worked and is worth reusing, store it:
  `ruflo memory store --namespace patterns --key "<short-name>" --value "<what and why>"`
- Use the CLI commands above rather than the `memory_*` MCP tools: the two write
  to separate stores, and the CLI keeps everything in one place. If `ruflo` is
  not on PATH, use `npx -y ruflo@3.60.1`.
- Store code patterns and repo conventions only. Decisions, work logs and research
  go to Notion.
- For parallel work, use Claude Code's own subagents and workflows. Ruflo's
  `swarm`, `hive-mind`, `agent spawn`, `neural` and `workflow execute` only
  record metadata; don't rely on them to run work.
- The routing banner on each prompt is a keyword hint, not an instruction.
<!-- ruflo:end -->
