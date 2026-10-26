---
name: ship
description: Slipway step 5. Commit the finished change in this repo's style, push, open the PR, watch CI and work through review comments.
disable-model-invocation: true
argument-hint: "[spec path; default: the newest in .slipway/specs/]"
---

# Ship (Slipway step 5)

Spec: $ARGUMENTS (if empty, the newest file in `.slipway/specs/`).

## 1. Check it's ready

- QA passed in /build-feature; if it didn't run, run `qa-reviewer` now.
- Docs: `README.md` and each touched module's `README.md` describe the new behavior, commands and outputs. Close gaps through `implementer`.
- `git status` shows only this feature's changes. If other work is mixed in, ask the user which files belong.
- On `main`, create `feat/<slug>` first.

## 2. Commit

Split the work into commits a reviewer can follow: usually one per user-visible change, with the backend and its web screen together. Each message follows CLAUDE.md: one plain sentence in the imperative saying what changes for the user, and a body only when the reason isn't obvious. Read `git log --format=%s -15` first to match the tone. Stage paths by name, never with `git add -A`.

## 3. Push and open the PR

Run `git push -u origin <branch>`, then `gh pr create --base main` with:
- Title: the main commit's subject.
- Body, in this repo's style (see PRs #14 and #16):
  - `## What`: a paragraph on what changes and why, then a bullet per area that starts with the **bold path**
  - `## Kept` or `## Not in this PR`: only when there's something to say
  - `## After merging`: manual steps (deploys, backfills, settings), if any

## 4. Watch CI

Wait with `gh pr checks <number> --watch` in the background. On a failure, read the log (`gh run view <id> --log-failed`), fix it through `implementer`, commit and push. After 2 rounds, ask the user.

## 5. Review comments

Read them with `gh pr view <number> --comments` and `gh api repos/{owner}/{repo}/pulls/<number>/comments`. For each, say whether to fix it or why not. Apply the fixes the user agrees with through `implementer`, then commit and push. Don't post replies or resolve threads without asking.

## 6. Finish

Tell the user the PR is ready to merge; merging is theirs. Offer to "save to Notion": a Work Log row for this project with Decisions, Open questions and Next steps.
