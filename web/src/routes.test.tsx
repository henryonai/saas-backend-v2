import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { routeTree } from './routeTree.gen';
import type { SessionUser } from './shared/auth/claims';
import { sessionStore } from './shared/auth/session';
import { TooltipProvider } from './shared/components/ui/tooltip';
import { json, OWNER } from './test/render';

vi.mock('./shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
}));

const order = {
  order_id: 'o-1', tenant_id: 'tenant-1', created_by: 'user-1', item: 'Green tea',
  quantity: 2, price_cents: 450, status: 'pending', created_at: '2026-10-20T08:00:00Z',
};
const invitation = {
  tenant_id: 'tenant-2', tenant_name: 'Bean Co', email: 'owner@example.com', role: 'member',
  invited_by: 'boss@example.com', created_at: '2026-10-20T08:00:00Z', expires_at: 1_900_000_000,
};

/** A stand-in for the HTTP API: "METHOD /path" -> response body. */
const API: Record<string, unknown> = {
  'GET /tenants/me': {
    tenant_id: 'tenant-1', name: 'Acme Coffee', owner_id: 'user-1', plan: 'free',
    subscription_status: 'active', seats_used: 2, created_at: '2026-10-01T08:00:00Z',
  },
  'GET /orders': { items: [order], next_cursor: null },
  'GET /orders/o-1': order,
  'GET /users': [
    { tenant_id: 'tenant-1', user_id: 'user-1', email: 'owner@example.com', role: 'owner', created_at: '2026-10-01T08:00:00Z' },
    { tenant_id: 'tenant-1', user_id: 'user-2', email: 'linh@example.com', role: 'member', created_at: '2026-10-02T08:00:00Z' },
  ],
  'GET /users/invitations': [{ ...invitation, tenant_id: 'tenant-1', email: 'minh@example.com' }],
  'GET /invitations': [invitation],
  'GET /billing': { plan: 'free', plan_name: 'Free', price_usd_month: 0, status: 'active', limits: { max_users: 3, max_orders: 100 }, manageable: true },
  'GET /notifications': [
    { notification_id: 'n-1', tenant_id: 'tenant-1', user_id: 'user-1', title: 'Order created', body: 'Your order for Green tea was created.', read: false, created_at: '2026-10-20T08:00:00Z' },
  ],
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${new URL(url, 'http://app.test').pathname}`;
    return key in API ? json(API[key]) : json({ message: 'Not Found' }, 404);
  }));
});

function renderAt(path: string, user: SessionUser | null = OWNER) {
  sessionStore.setState(user ? { status: 'signed-in', user, reason: null } : { status: 'signed-out', user: null, reason: null });
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </TooltipProvider>,
  );
  return router;
}

describe('pages', () => {
  it.each([
    ['/', 'Acme Coffee', 'Recent orders'],
    ['/orders', 'Orders', 'Green tea'],
    ['/orders/o-1', 'Green tea', 'Mark as paid'],
    // set up or not (VITE_ASSISTANT_URL in .env.local), the page says what it is for
    ['/assistant', 'Assistant', "Ask about your workspace's orders and usage. It works with your access, so it sees only what you can."],
    ['/team', 'Team', 'linh@example.com'],
    ['/billing', 'Billing', 'Upgrade to Pro'],
    ['/notifications', 'Notifications', 'Your order for Green tea was created.'],
    ['/account', 'Account', 'Change password'],
  ])('%s renders with its data', async (path, heading, content) => {
    renderAt(path);
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    expect((await screen.findAllByText(content)).length).toBeGreaterThan(0);
  });

  it('shows a missing order as an error, not a crash', async () => {
    renderAt('/orders/nope');
    expect(await screen.findByText('Not Found')).toBeInTheDocument();
  });
});

describe('route guards', () => {
  it('sends a signed-out visitor to sign-in, remembering where they were going', async () => {
    const router = renderAt('/orders', null);
    expect(await screen.findByText('Welcome back. Sign in with your email and password.')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(router.state.location.search).toEqual({ redirect: '/orders' });
  });

  it('sends a user without a workspace to the welcome page, with their invitations', async () => {
    const router = renderAt('/orders', { ...OWNER, tenantId: null, role: null, plan: null });
    expect(await screen.findByText('“Bean Co”')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/welcome');
  });

  it('keeps a user with a workspace out of the welcome and sign-in pages', async () => {
    const router = renderAt('/welcome');
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    const again = renderAt('/sign-in');
    await waitFor(() => expect(again.state.location.pathname).toBe('/'));
  });

  it('hides owner-only billing actions from members', async () => {
    renderAt('/billing', { ...OWNER, role: 'member' });
    expect(await screen.findByText('Only the workspace owner can change the plan.')).toBeInTheDocument();
    expect(screen.queryByText('Upgrade to Pro')).not.toBeInTheDocument();
    expect(screen.queryByText('Manage billing')).not.toBeInTheDocument();
  });

  it('lets the owner open the billing portal', async () => {
    renderAt('/billing');
    expect(await screen.findByRole('button', { name: 'Manage billing' })).toBeInTheDocument();
  });
});
