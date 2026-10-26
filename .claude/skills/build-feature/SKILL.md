---
name: build-feature
description: Slipway step 2. Build an approved spec with subagents (implementer and tester for each plan step, then qa-reviewer, plus security-reviewer when sensitive areas change), looping on fixes until the checks pass.
when_to_use: The user approved a spec from /brainstorm and wants it built. Never before a spec is approved.
argument-hint: "[spec path; default: the newest in .slipway/specs/]"
---

# Build feature (Slipway step 2)

Spec: $ARGUMENTS (if empty, the newest file in `.slipway/specs/`; if several are recent, ask which).

You coordinate and the subagents do the work. Keep their reports short in your context, and tell the user in one line as each step finishes.

## 0. Before starting

- Read the spec. If it has no Behavior or Plan section, stop and suggest /brainstorm.
- Run `git status`. On `main`, create `feat/<slug>` first. If there are uncommitted changes that aren't part of this feature, tell the user and leave them alone.
- Note the starting commit (`git rev-parse HEAD`): the reviewers diff against it.

## 1. Build each plan step

Go in plan order. Steps the plan marks as parallel, and that touch different files, can run at the same time (at most 2 implementers at once).

For a feature step:
1. `implementer`: give it the spec path, the step number, and whatever earlier steps changed that it needs to know.
2. `tester`: give it the spec path, the step number and the files the implementer changed.

For a bug, run `tester` first to write a test that fails for the reported reason, then `implementer` to make it pass.

When tests fail:
- The tester says the code is wrong: send the failure to `implementer` for the same step, then rerun `tester`. At most 2 rounds per step.
- The tester says the spec is wrong or unclear, or 2 rounds didn't fix it: stop and ask the user.

## 2. Review

When every step passes:
- Always run `qa-reviewer`, with the spec path and the starting commit.
- In parallel, run `security-reviewer` when the diff touches anything CLAUDE.md lists as security-sensitive, or the spec has a security risk flag.

## 3. Fix what blocks

Send blocking findings to `implementer` (grouped by file), then `tester` for the affected behavior, then rerun only the reviewer that raised them. At most 2 rounds, then ask the user. List non-blocking findings for the user; don't fix them unasked.

## 4. Report

- What was built, one line per step
- Tests added, and the final check results
- The QA verdict, and the security verdict (or "not run: no sensitive areas changed")
- Open findings, and anything you decided without the user
- Next: `/deploy-dev` to try it on dev, or `/ship` if it doesn't need a dev check

Don't commit, push or deploy in this step.
