# web: the React app

The frontend of the backend in this repository: sign-up and sign-in (Cognito),
creating or joining a workspace, orders, the team and its invitations,
billing, and notifications. English and Vietnamese, light and dark.

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
call the API directly.

## Deploying

Each stack hosts the app: a private S3 bucket behind CloudFront (the `Web*`
resources in `template.yaml`), at the stack's `WebUrl` output.

```bash
./scripts/deploy.sh saas-dev   # build with the stack's settings, upload, clear the CDN cache
```

Deploy the backend first (`sam deploy`), since it creates the bucket. The build
uses the stack's outputs, not `.env.local`. Files with a hash in their name
are cached for a year; `index.html` is checked on every visit, so a new
deploy shows at once.

`AppUrl` (checkout and invitation links) and `CorsOrigin` default to `WebUrl`.
To serve the app from your own domain, add the domain and its certificate to
`WebDistribution`, then set both parameters to it.

## Layout

```
src/
  main.tsx              loads the session, then renders; clears the cache when the workspace changes
  router.tsx            the router (routeTree.gen.ts is generated from routes/)
  routes/               file routes, thin: each renders a page from features/
    _public/            sign-in, sign-up, confirm, forgot-password (signed out only)
    _onboarding/        welcome: create a workspace or accept an invitation (signed in, no workspace)
    _app/               the app (signed in, with a workspace): overview, orders, team, billing, ...
    dev-checkout.tsx    DevPaymentProvider's checkout page
  features/<area>/      one area of the app (see below)
  shared/
    api/                api() client, query keys, types; generated/ comes from the backend
    auth/               Amplify setup, the session (claims of the ID token), route guards
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

- **The session is the ID token.** Its claims say who the user is and their
  workspace, role and plan (all three or none). The routes use them to send
  the user to sign-in, to the welcome page, or into the app. After anything
  that changes them (creating or joining a workspace, an upgrade) the app gets
  new tokens, so the login trigger writes the new claims.
- **401** gets one retry with new tokens. If that fails, or the session was
  revoked (the backend revokes it when someone is removed, demoted or leaves),
  the user is signed out and the sign-in page says why.
- **402** shows the backend's message with "See plans" for the owner, and
  "ask the owner" for everyone else. Other errors show the backend's message
  (in English); network and server errors are translated.
- **Upgrades** happen at the payment provider, which tells the backend by
  webhook. On return, the billing page asks `/billing` every 3 seconds (for up
  to 2 minutes) until the new plan shows, then refreshes the token.
- **Role checks in the UI** only hide what the user can't do; the backend
  decides.

## Testing a checkout

On a stack with `PaymentProvider=stripe` (test mode), checkout is Stripe's
page: pay with Stripe's test card `4242 4242 4242 4242`, any future date and
any CVC. "Manage billing" opens Stripe's portal, where cancelling and plan
changes can be tried too.

With `PaymentProvider=dev`, `DevPaymentProvider` sends the owner to
`/dev-checkout` instead of a payment page. Only a signed webhook changes the plan, and the browser can't sign one,
so the page shows the command that does, from the repository root:

```bash
uv run python scripts/dev-pay.py --config-env dev --tenant <tenant_id> --plan pro
uv run python scripts/dev-pay.py --config-env dev --tenant <tenant_id> --event payment.failed
```

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

- The assistant's chat (it streams from AgentCore, not the HTTP API)
- Changing the email address, turning on MFA (sign-in already handles an
  authenticator code)
