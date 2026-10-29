# Data model

Every table at a glance: what it holds, its keys, indexes, TTLs and streams, and
which query serves which route or job. Each module's `repository.py` starts with
its own key design and stays the source of truth; update both when keys change.

## How tenants' data is stored

All workspaces share each table (AWS calls this the pool model). Every key that
belongs to a workspace starts with `TENANT#<tenant_id>`, and one user's data in a
workspace with `TENANT#<tenant_id>#USER#<user_id>`, so a query can never reach
another workspace: the tenant id comes from the login token, never from the
request body. Other keys are lookups and system records: `USER#<id>`
(memberships), `EMAIL#` (the invitations index), `OUTBOX#` (events waiting to be
published), `IDEMPOTENCY#` (retried creates and processed events).

Each module owns one table and is the only code that touches it. Every table is
on-demand (`PAY_PER_REQUEST`), has point-in-time recovery, is kept when the stack
is deleted (`DeletionPolicy: Retain`) and is deletion-protected in prod. Rules
that must hold under concurrent requests (seats, limits, memberships, one
`ExportReady`) are DynamoDB conditions, not read-then-write code.

A module that changes a workspace's data keeps a copy of the workspace's
status (`TENANT#<t>` / `STATUS`, `shared/workspace_status.py`) from accounts'
and admin's events, and refuses changes while it says suspended or being
deleted: tokens still list the workspace for up to 15 minutes. No item means
active.

| Table | Module | Index | TTL attribute | Stream |
|---|---|---|---|---|
| Accounts | accounts | `gsi1` (ALL) | `expires_at` | NEW_IMAGE → outbox relay |
| Orders | orders | none | `expires_at` | NEW_IMAGE → outbox relay |
| Billing | billing | none | `expires_at` | NEW_IMAGE → outbox relay |
| Notifications | notifications | none | `expires_at` | none |
| Audit | audit | none | `expires_at` | none |
| Admin | admin | none | `expires_at` | NEW_IMAGE → outbox relay |
| Assistant | assistant (optional) | `ConversationsByUser` (INCLUDE) | `expires_at` | OLD_IMAGE → purge worker |
| Assistant checkpoints | assistant (optional) | none | `ttl` | none |

The outbox relay's stream filter passes only inserted `OUTBOX#` items; the
assistant's passes only TTL's removal of a conversation's `META`.

## Accounts

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>` | `PROFILE` | the workspace: name, owner, plan copy, `seats_used`, `status`, `delete_after` | no |
| `TENANT#<t>` | `USER#<u>` | a member and their role | no |
| `TENANT#<t>` | `INVITE#<email>` | a pending invitation | when it expires |
| `TENANT#<t>` | `INVITES_SENT#<day>` | invitations sent that day (limit) | 2 days |
| `TENANT#<t>` | `JOB#<job_id>` | an export of the workspace | 7 days |
| `TENANT#<t>` or `USER#<u>` | `EXPORTS#<day>` | exports started that day (limit) | 2 days |
| `USER#<u>` | `WORKSPACE#<t>` | one of the user's workspaces | no |
| `USER#<u>` | `WORKSPACES` | how many (at most `MAX_WORKSPACES_PER_USER`) | no |
| `USER#<u>` | `JOB#<job_id>` | an export of the user's own data | 7 days |
| `USER#<u>` | `IDEMPOTENCY#<u>#<key>` | the workspace a `POST /tenants` with that key made | 24 hours |
| `OUTBOX#<event_id>` | `OUTBOX` | an event waiting to be published | 7 days |

`gsi1` (sparse: only items that set it):

| gsi1pk | gsi1sk | On | Serves |
|---|---|---|---|
| `EMAIL#<email>` | `<created_at>#<t>` | invitations | `GET /invitations` (invitations to the signed-in email) |
| `DELETING` | `<delete_after>#<t>` | deleted workspaces | the hourly purge job: workspaces due |

| Access pattern | Query |
|---|---|
| Login trigger: every workspace of a user, with role and plan | Query `USER#<u>` begins_with `WORKSPACE#`, then BatchGet each `PROFILE` and `USER#<u>` |
| `GET /tenants`, `GET /tenants/me` | the same; GetItem `PROFILE` |
| `GET /users` (members) | Query `TENANT#<t>` begins_with `USER#` |
| `GET /users/invitations` | Query `TENANT#<t>` begins_with `INVITE#` |
| Accept an invitation (seat, membership, limit) | one transaction: membership, count, member, invitation, seats |
| `GET /exports/workspace`, `/exports/me` (newest) | Query `TENANT#<t>` or `USER#<u>` begins_with `JOB#`, newest first, limit 1 |
| An export's part arrived | UpdateItem `JOB#` ADD `done` |
| Purge a deleted workspace | Query `gsi1` `DELETING` ≤ now; delete `TENANT#<t>` |

## Orders

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>` | `ORDER#<order_id>` | an order (ids sort by creation time) | no |
| `TENANT#<t>` | `COUNTER` | `order_count`, checked against the plan's `max_orders` | no |
| `TENANT#<t>` | `IDEMPOTENCY#<u>#<key>` | the order a `POST /orders` with that key made | 24 hours |
| `TENANT#<t>` | `STATUS` | the workspace's status (`active`, `suspended`, `deleting`; no item: active): changes are refused unless active | no (deleted with the workspace) |
| `OUTBOX#<event_id>` | `OUTBOX` | an event waiting to be published | 7 days |

| Access pattern | Query |
|---|---|
| `GET /orders` (newest first, paged) | Query `TENANT#<t>` begins_with `ORDER#`, descending |
| `GET /orders/<id>`, `PATCH .../status` | GetItem; UpdateItem with the expected status as a condition |
| `POST /orders` | one transaction: counter (limit), order, outbox, idempotency key, a condition check on `STATUS` |

## Billing

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>` | `SUBSCRIPTION` | plan, status, revision, the provider's ids; while the workspace is being deleted, `deleting`, `deleting_at` and `renewal_stopped`; `deleted_at` once purged | 90 days after the workspace is purged |
| `TENANT#<t>` | `STATUS` | the workspace's status (`active`, `suspended`, `deleting`; no item: active): changes are refused unless active | no (deleted with the workspace) |
| `OUTBOX#<event_id>` | `OUTBOX` | an event waiting to be published | 7 days |

Access: GetItem by tenant (`GET /billing`, webhooks, checkout); a webhook's
change is one conditional transaction on `revision`.

## Notifications

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>#USER#<u>` | `NOTIF#<notification_id>` | one in-app notification | no (deleted with the member or the workspace) |
| `IDEMPOTENCY#<function>` | `<hash>` | events the worker handled (Powertools) | 7 days |

Access: `GET /notifications` queries the user's partition newest first; the
worker deletes a partition on `MemberRemoved` and `TenantDeleted`.

## Audit

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>` | `AUDIT#<occurred_at>#<event_id>` | who did what (an event about the workspace) | a year (`AUDIT_RETENTION_DAYS`) |

Access: `GET /audit` queries the workspace's partition newest first; the
listener writes with `attribute_not_exists`, so a repeated event adds nothing.

## Assistant (optional)

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANT#<t>` | `USAGE#<YYYY-MM>` | the workspace's messages and tokens that month | about 13 months |
| `TENANT#<t>` | `USER#<u>#HOUR#<YYYY-MM-DDTHH>` | messages a user sent that hour | after the hour |
| `TENANT#<t>` | `USER#<u>#UPLOADS#<YYYY-MM-DDTHH>` | files they uploaded that hour | after the hour |
| `TENANT#<t>` | `STATUS` | the workspace's status (`active`, `suspended`, `deleting`; no item: active): changes are refused unless active | no (deleted with the workspace) |
| `TENANT#<t>#USER#<u>#CONV#<thread>` | `META` | a conversation, and its run while one answers | `CONVERSATION_DAYS` after its last message, or never |
| `…#CONV#<thread>` | `MSG#<000001>` | a message at its position | with the conversation |
| `…#CONV#<thread>` | `BRANCH#<0001>#MSG#<000004>` | a message in a later version | with the conversation |
| `TENANT#<t>#USER#<u>#MEMORY` | `MEM#<id>`, `SETTINGS` | what it remembers about the user, and whether it does | no |
| `TENANT#<t>#USER#<u>#FILES` | `FILE#<file_id>`, `NAMED#<thread>#<name>` | the user's files (bytes in S3) | pending uploads after a day |

`ConversationsByUser`: gsi1pk `TENANT#<t>#USER#<u>#CONVS`, gsi1sk `<updated_at>`,
on `META` only, with only the fields the list shows. It serves the conversation
list (newest first) and the purge of a removed member's conversations.

The checkpoint table (`PK`/`SK`, LangGraph's own layout) holds each agent's state
per thread, expiring after 30 days (`ttl`); checkpoints over 350 KB go to S3.

## Outside DynamoDB

| Where | What | Kept |
|---|---|---|
| Exports bucket `exports/<kind>/<owner>/<job>/<module>.json` | an export's parts | 7 days |
| Assistant files bucket | the user's files and their versions | until deleted |
| Assistant checkpoint bucket | checkpoints over 350 KB | with their thread |
| Cognito user pool | logins (email, password, MFA) | until the account is deleted |

## Admin

| pk | sk | What | Expires |
|---|---|---|---|
| `TENANTS` | `<tenant_id>` | a workspace's summary (name, owner, plan, status, members, orders) | no (deleted when the workspace is purged) |
| `EVENT#<event_id>` | `EVENT` | an event already applied to the summaries | 7 days |
| `LOG` | `<at>#<action_id>` | something a platform admin did | no |
| `OUTBOX#<event_id>` | `OUTBOX` | an event waiting to be published | 7 days |

Access: platform admins page through `TENANTS` (one partition, read only by
them) and `LOG` (newest first). The table's stream feeds the outbox relay.
