# Backlog

Work found while building something else, and deferred. Each entry says what it is, why it
matters and what it costs, so it can be decided later without working it out again. When an
entry is picked up it becomes a plan in this folder, and moves to Done with a link to it.

## Open

- [ ] **The assistant's answers are checked against a fixed set of conversations before a change
  to its prompts, tools or middleware ships.**

  The assistant's tests (`modules/assistant/tests/`) run on scripted fake models, so they prove
  the code works but not that the assistant still answers well. A change to
  `modules/assistant/src/assistant/agents/*.md`, its tools, its middleware or `llm.py` can make
  the answers worse with every test green.

  **Cost now:** 10–15 test conversations drawn from `modules/assistant/README.md` (edit, answer
  again, stop, memory, the bash tool, delegation), a rubric for each, and a runner against the
  local assistant (`main.py`) on the dev stack's tables. Each run spends real model calls.
  **Cost if deferred:** a regression in the answers is found by users, not before the merge.

## Done
