---
name: e2e
description: Slipway step 4. Check a change end to end on the dev stack, with the API smoke test and, for web changes, a browser walk-through of the spec's behavior.
when_to_use: After /deploy-dev succeeds, or when the user asks for an end-to-end check on dev. Never against prod.
argument-hint: "[spec path; default: the newest in .slipway/specs/]"
---

# End-to-end check on dev (Slipway step 4)

Spec: $ARGUMENTS (if empty, the newest file in `.slipway/specs/`). Only `saas-dev`.

## API

Run `uv run scripts/smoke-test.py --stack saas-dev`. It needs AWS credentials: if they're missing or expired, ask the user to refresh them (for example `! aws sso login`) and wait. It creates test users, calls every endpoint, and saves the users' logins in `.aws-sam/smoke-users.json`.

## Web (only when the change touches `web/`)

1. Run `cd web && ./scripts/env-from-stack.sh saas-dev`, start `pnpm dev` in the background, and wait for http://localhost:5173.
2. Load the claude-in-chrome skill and open http://localhost:5173 in a new tab.
3. Signing in goes to Cognito, not to localhost, so ask the user to sign in in that tab with a test user from `.aws-sam/smoke-users.json`. Don't type the credentials yourself.
4. Walk through each Behavior item in the spec that a user can see. When UI text changed, check both English and Vietnamese. Read the console for errors.
5. Stop the dev server and close the tab when you're done.

## Report

A table of each Behavior item: pass, fail, or not checked (and why). Then the smoke test result and any console errors. For each failure, say whether it looks like the code (a fix round with `/build-feature`) or the environment (credentials, stack outputs, data).
