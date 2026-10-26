import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import '@/shared/i18n';
import type { SessionUser } from '@/shared/auth/claims';
import { sessionStore } from '@/shared/auth/session';
import { TooltipProvider } from '@/shared/components/ui/tooltip';

export const OWNER: SessionUser = {
  userId: 'user-1',
  email: 'owner@example.com',
  emailVerified: true,
  workspaces: [{ tenantId: 'tenant-1', role: 'owner', plan: 'free' }],
  tenantId: 'tenant-1',
  role: 'owner',
  plan: 'free',
};

/** Signed up, but in no workspace yet. */
export const NEWCOMER: SessionUser = { ...OWNER, workspaces: [], tenantId: null, role: null, plan: null };

/** Renders inside the app's providers (a router, a fresh query cache), signed in as `user`. */
export async function renderWithApp(ui: ReactNode, { user = OWNER }: { user?: SessionUser | null } = {}) {
  sessionStore.setState(user ? { status: 'signed-in', user, reason: null } : { status: 'signed-out', user: null, reason: null });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createRouter({
    routeTree: createRootRoute({ component: () => <>{ui}</> }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  await router.load();
  return render(
    <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </TooltipProvider>,
  );
}

/** A fetch Response with a JSON body. */
export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
