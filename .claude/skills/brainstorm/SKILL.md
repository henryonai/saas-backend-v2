---
name: brainstorm
description: Slipway step 1. Turn a problem, feature idea or bug into an agreed spec and step plan before any code is written.
when_to_use: The user describes a new problem, feature idea, change request or bug, and no spec for it exists yet. Not for a small fix the user has already specified exactly.
argument-hint: "[the problem, in a sentence or two]"
---

# Brainstorm (Slipway step 1)

Problem: $ARGUMENTS

Leave this step with a spec and plan the user approves, saved in `.slipway/specs/`. Write no production code here.

## 1. Restate

Say the problem back in one or two sentences, and what "done" looks like. If it's a bug, say so: you'll diagnose it before designing anything.

## 2. Gather context before asking anything

- The Notion project page linked in CLAUDE.md, and its 3 most recent Work Log rows.
- The code the change touches. Use the Explore agent for broad searches, and read the key files yourself.
- For a bug: reproduce it (a failing test, a command, or logs) and find the root cause. Don't propose a fix you can't tie to a cause.

## 3. Ask only what changes the design

Use AskUserQuestion for decisions the code and context can't settle: scope, what users will see, trade-offs. Ask at most 3 questions at a time, each with a recommended option. Where there's an obvious default, state it instead of asking.

## 4. Compare approaches

Give 2–3 approaches only when they differ in a way that matters (risk, cost, what users see, data migration), with one line each on what it costs, and recommend one. If one approach is clearly right, say so and move on.

## 5. Write the spec

Save it as `.slipway/specs/<YYYY-MM-DD>-<short-slug>.md`:

```markdown
# <Title>

## Problem

## Behavior
<!-- Acceptance criteria: checkable bullets, each one something a test can prove. -->

## Out of scope

## Design
<!-- The chosen approach and why; data shapes and API changes. -->

## Plan
<!-- Numbered steps. For each: files, what changes, tests to add.
     Mark steps that can run in parallel because they touch different files. -->

## Risk flags
<!-- Any that apply: security-sensitive area (see CLAUDE.md), API change (gen:api),
     stored item shape change (backfill), dependency change, UI text (en + vi), docs to update. -->
```

Keep each step small enough for one agent run: roughly one module or one screen.

## 6. Get approval

Show the Behavior and Plan sections, and ask the user to approve or change them. Once approved, the next step is `/build-feature <spec path>`; start it only when the user says so.

Decisions worth keeping go to Notion only when the user says "save to Notion".
