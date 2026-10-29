# web: the React app

The frontend of the backend in this repository: sign-up and sign-in (Cognito),
creating or joining a workspace, orders, the team and its invitations,
billing, notifications, and a chat with the AI assistant. English and
Vietnamese, light and dark.

## Stack

Vite, React 19, TypeScript, [TanStack Router](https://tanstack.com/router)
(file-based routes) and [TanStack Query](https://tanstack.com/query),
[shadcn/ui](https://ui.shadcn.com) on Radix with Tailwind v4, zustand for the
session and theme, i18next, Vitest with Testing Library. Cognito through
Amplify (Auth only): the app client allows SRP sign-in only, which Amplify
does, and Amplify keeps and refreshes the tokens.

## Running it

It needs a deployed stack (`sam deploy --config-env dev`, see the root README).

```bash
pnpm install
./scripts/env-from-stack.sh saas-dev   # writes .env.local from the stack's outputs
pnpm dev                               # http://localhost:5173
```

The dev stack allows any origin (`CorsOrigin=*`), so a local dev server can
call the API directly. The assistant's endpoint (AgentCore) answers any
origin's CORS preflight itself. `VITE_ASSISTANT_URL` is optional: a stack
deployed without the assistant has no `AssistantUrl` output, and then only
the Assistant page says it isn't set up.

## Deploying

Each stack hosts the app: a private S3 bucket behind CloudFront (the `Web*`
resources in `template.yaml`), at the stack's `WebUrl` output. The deployed app
calls the API on that same address, at `/api` (CloudFront sends `/api/*` to the
HTTP API, uncached, with every header), so it needs no CORS; `WebApiUrl` is
that address. A local dev server calls the HTTP API directly (`ApiUrl`).

```bash
./scripts/deploy.sh saas-dev   # build with the stack's settings, upload, clear the CDN cache
```

Deploy the backend first (`sam deploy`), since it creates the bucket. The build
uses the stack's outputs, not `.env.local`. Files with a hash in their name
are cached for a year; `index.html` is checked on every visit, so a new
deploy shows at once.

`AppUrl` (checkout and invitation links) defaults to `WebUrl`. To serve the app
from your own domain, add the domain and its certificate to `WebDistribution`,
then set `AppUrl` to it; `/api` follows the domain.

## Layout

```
src/
  main.tsx              loads the session, then renders; when the workspace changes, clears the cache and renders the page again
  router.tsx            the router (routeTree.gen.ts is generated from routes/)
  routes/               file routes, thin: each renders a page from features/
    _public/            sign-in, sign-up, confirm, forgot-password (signed out only)
    _onboarding/        welcome: create a workspace or accept an invitation (signed in, no workspace)
    _app/               the app (signed in, with a workspace): overview, orders, assistant, team, ...
  features/<area>/      one area of the app (see below)
  shared/
    api/                api() client, query keys, types; generated/ comes from the backend
    auth/               Amplify setup, the session (claims of the ID token), route guards, WorkspaceScope
    components/ui/      vendored shadcn/ui (`pnpm dlx shadcn@latest add <component>`)
    components/         the app's own building blocks
    i18n/               i18next, locales/en.json and vi.json
    lib/, theme/
```

`shared/` never imports from `features/`.

Every feature has the same shape, with only the parts it needs (no empty
files or folders):

```
features/orders/
  api.ts                  plain calls, one function per endpoint, no React (like a module's api.py)
  hooks.ts                TanStack Query hooks: query keys, cache updates, polling
  status.ts               pure logic, next to api.ts
  orders-page.tsx         pages at the root: routes/ imports them
  order-detail-page.tsx
  components/             everything else that renders
  *.test.ts(x)            next to what they test
```

Components and pages use the hooks, not `api.ts`; plain code that needs the
API outside React (the billing page's wait for the new plan) calls `api.ts`.

## How it uses the backend

| Page | Calls |
|---|---|
| Sign-up, confirm, sign-in, reset password | Cognito (Amplify) |
| Welcome | `POST /tenants`, `GET /invitations`, `POST /invitations/{tenant_id}/accept` and `/decline` |
| Overview | `GET /tenants/me`, `GET /orders?limit=5`, `GET /notifications` |
| Orders | `GET /orders` (cursor pages), `POST /orders`, `GET /orders/{id}`, `PATCH /orders/{id}/status` |
| Team | `GET /users`, `PATCH /users/{id}/role`, `DELETE /users/{id}`, `GET /users/invitations`, `POST /users/invite`, `DELETE /users/invitations/{email}` |
| Billing | `GET /billing`, `POST /billing/checkout`, `POST /billing/portal` ("Manage billing") |
| Notifications (and the bell, every 45 s) | `GET /notifications`, `POST /notifications/{id}/read` |
| Assistant | sending: `POST /assistant/conversations/{thread_id}/messages`, then the answer from AgentCore's `/invocations` (`AssistantUrl`), AG-UI over SSE; Stop: `POST /assistant/conversations/{thread_id}/runs/{run_id}/cancel`; the list: `GET /assistant/conversations`, `GET`, `PATCH` and `DELETE /assistant/conversations/{thread_id}`; older messages: `GET /assistant/conversations/{thread_id}/messages?before=`; versions: `POST .../messages/{message_id}/version`; memories: `/assistant/memories`; files: `POST /assistant/files` (then S3), `GET /assistant/files`, `GET` and `DELETE /assistant/files/{file_id}` |

- **The session is the ID token.** Its claims say who the user is and every
  workspace they belong to, with their role and its plan. The routes use them
  to send the user to sign-in, to the welcome page, or into the app. After
  anything that changes them (creating, joining or leaving a workspace, an
  upgrade) the app gets new tokens, so the login trigger writes the new claims.
- **Each tab works in one workspace.** The switcher in the sidebar changes it
  for that tab only (`sessionStorage`; a new tab starts in the last one used),
  creates workspaces and accepts invitations. Every API call sends it as
  `X-Tenant-Id`, and the assistant's runs as `forwardedProps.tenantId`.
  Switching clears the query cache, so nothing from one workspace shows in
  another.
- **401** gets one retry with new tokens. If that fails, or the session was
  revoked (the backend revokes it when someone is removed or their role
  changes), the user is signed out and the sign-in page says why.
- **402** shows the backend's message with "See plans" for the owner, and
  "ask the owner" for everyone else. Other errors show the backend's message
  (in English); network and server errors are translated.
- **Upgrades** happen at the payment provider, which tells the backend by
  webhook. On return, the billing page asks `/billing` every 3 seconds (for up
  to 2 minutes) until the new plan shows, then refreshes the token.
- **Role checks in the UI** only hide what the user can't do; the backend
  decides.

## The assistant's chat

As in ChatGPT, the server keeps everything and knows what is running
(modules/assistant/README.md, "Calling it from the frontend"). Sending saves
the message with the run that will answer it (the HTTP API), then
`features/assistant/` streams that run from AgentCore in
[AG-UI](https://docs.ag-ui.com), the assistant's only contract, through the
official client, `@ag-ui/client`. The run goes on whatever the page does.

- `api.ts`: `postMessage` and `cancelRun` on the HTTP API, and
  `AssistantAgent`, an `HttpAgent` that sends the ID token, the threadId as
  the AgentCore session header, and the run (`runAgent({ runId })`), no
  messages: the server answers the one it saved. `runError()` reads
  AgentCore's own errors, which come with an HTTP error status (the client
  throws them) rather than in the stream; `requestError()` the HTTP API's.
- `conversation.ts`: the conversation on screen (a zustand store, like the
  session): the messages the client assembles from the events, or a
  conversation reopened from its history (`openConversation`), which follows
  its run if its answer is still coming: AgentCore sends what it said so far,
  then the rest. It survives leaving the page; signing out or changing
  workspace clears it. A token turned down (`UNAUTHORIZED`, `ACCESS_DENIED`)
  gets one retry with new tokens, like the API client.
- `transcript.ts`: messages to what the page shows: one turn per answer, its
  text (Markdown) and tool calls in order, each with its input and result.
- Errors: the plan's limit is the send's 402, which shows the upgrade notice;
  a send while an answer is coming (another tab) is a 409. The run's are
  `RUN_ERROR` codes, which say what happened, with "Try again" when asking
  again may help (the same message, which the server doesn't save twice).
- "Stop" stops the answer on screen at once and on the server, which keeps
  what it wrote. Leaving a conversation doesn't stop its answer: reopened,
  it is followed again.

**Conversations** (`hooks.ts`, `components/conversation-list.tsx`): the list
beside the chat (a side panel on a phone) comes from the HTTP API
(`/assistant/conversations`), most recently used first.

- `/assistant` starts a new conversation; its first message moves the URL to
  `/assistant/<threadId>` and puts it at the top of the list, titled with that
  message until the answer ends and the list is read again with the model's
  title.
- `/assistant/<threadId>` shows the conversation: the one streaming now, or
  its history from the API (on a reload, or from the list), in the same AG-UI
  messages the stream gave, and its answer if one is still coming (`run_id`).
  Sending continues it with that threadId. A conversation that was deleted or
  expired says so. A long one opens at its latest 200 messages; "Show
  earlier messages" reads back a page at a time (`loadEarlier`, with the
  `earlier_cursor` the API gave), keeping the user's place on screen.
- Editing a message, or asking for an answer again (Regenerate, on any
  answer), keeps the old version on the server, as in ChatGPT: "‹ 2/3 ›"
  under the message (`versions`) shows another, and what followed it
  (`switchVersion`). A new version is numbered once its answer is in.
- Rename (a dialog) and delete (a confirmation) are in each item's menu.
  Deleting the open conversation starts a new one.

**Files** (`components/composer.tsx`, `components/files.tsx`, and the Files
page, `features/files/`): the paperclip (or a drop, or a paste) uploads a
file at once, straight to S3 with the presigned POST the API gives
(`startUpload`, `uploadFile`), and the message names it (`attachments`). A
message shows its files (images as themselves), and an answer the files an
agent saved, to download (`downloadFile` asks for a fresh link). The Files
page lists them all, by source and kind, with Download, Delete, and a link
to the conversation.

**Memory** (`components/memory-dialog.tsx`, the Memory button beside the
chat): what the assistant remembers about the user across conversations
(`/assistant/memories`), read each time it opens, as in ChatGPT's settings:
memory on or off, add, edit, delete, and Forget everything. When the
assistant saves one in a chat, its tool call says "Saved to memory".

The AG-UI client (with rxjs) loads with the Assistant page only.

## Testing a checkout

Checkout is Stripe's page (test mode on dev): pay with Stripe's test card `4242 4242 4242 4242`, any future date and
any CVC. "Manage billing" opens Stripe's portal, where cancelling and plan
changes can be tried too.

## Generated code

```bash
pnpm gen:api   # after changing a module's schemas or shared/plans.py
```

It runs `scripts/export-openapi.py` and writes `src/shared/api/generated/`:
types for each module's API, and `plans.json` (the plan catalog). Commit
them; CI regenerates them and fails if they differ. `shared/api/types.ts`
names the types the app uses.

## Translations

Every string is in `src/shared/i18n/locales/en.json` and `vi.json`. Keys are
checked at compile time against the English file, and a test checks that both
files have the same keys and `{{variables}}`.

## Scripts

`pnpm dev` · `pnpm build` · `pnpm typecheck` · `pnpm lint` · `pnpm test` ·
`pnpm gen:api`

## Not built yet

- Searching the assistant's conversations
- Changing the email address, turning on MFA (sign-in already handles an
  authenticator code)
