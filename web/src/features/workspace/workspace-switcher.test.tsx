import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/shared/api/client';
import type { SessionUser } from '@/shared/auth/claims';
import { sessionStore } from '@/shared/auth/session';
import { SidebarProvider } from '@/shared/components/ui/sidebar';
import { json, OWNER, renderWithApp } from '@/test/render';
import { WorkspaceSwitcher } from './workspace-switcher';

const IN_TWO: SessionUser = {
  ...OWNER,
  workspaces: [
    { tenantId: 'tenant-1', role: 'owner', plan: 'free' },
    { tenantId: 'tenant-2', role: 'member', plan: 'pro' },
  ],
};

// A new token after creating "Tea House": it lists that workspace too.
vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return {
    ...actual,
    getIdToken: vi.fn().mockResolvedValue('id-token'),
    refreshSession: vi.fn(async () => {
      const user = actual.sessionStore.getState().user!;
      const workspaces = [...user.workspaces, { tenantId: 'tenant-3', role: 'owner' as const, plan: 'free' }];
      const refreshed = { ...user, workspaces };
      actual.sessionStore.setState({ status: 'signed-in', user: refreshed, reason: null });
      return refreshed;
    }),
  };
});

const fetchMock = vi.fn<typeof fetch>();
let invitations: unknown[] = [];

beforeEach(() => {
  invitations = [];
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://app.test').pathname;
    if (path === '/tenants' && init?.method === 'POST') return json({ tenant_id: 'tenant-3', name: 'Tea House' }, 201);
    if (path === '/tenants') {
      return json([
        { tenant_id: 'tenant-1', name: 'Acme Coffee', role: 'owner', plan: 'free' },
        { tenant_id: 'tenant-2', name: 'Bean Co', role: 'member', plan: 'pro' },
      ]);
    }
    if (path === '/tenants/me') return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', plan: 'free' });
    if (path === '/invitations') return json(invitations);
    return json({ message: 'not found' }, 404);
  });
});

async function openMenu() {
  const user = userEvent.setup();
  await renderWithApp(
    <SidebarProvider>
      <WorkspaceSwitcher />
    </SidebarProvider>,
    { user: IN_TWO },
  );
  await user.click(await screen.findByRole('button', { name: 'Switch workspace' }));
  return user;
}

describe('WorkspaceSwitcher', () => {
  it('lists the workspaces and switches this tab to another', async () => {
    const user = await openMenu();
    await user.click(await screen.findByRole('menuitem', { name: /Bean Co/ }));

    await waitFor(() => expect(sessionStore.getState().user).toMatchObject({ tenantId: 'tenant-2', role: 'member', plan: 'pro' }));
    expect(sessionStorage.getItem('saas.workspace')).toBe('tenant-2'); // this tab
    expect(localStorage.getItem('saas.lastWorkspace')).toBe('tenant-2'); // where a new tab starts
  });

  it('sends the chosen workspace with the next request', async () => {
    const user = await openMenu();
    await user.click(await screen.findByRole('menuitem', { name: /Bean Co/ }));
    await waitFor(() => expect(sessionStore.getState().user?.tenantId).toBe('tenant-2'));

    fetchMock.mockClear();
    await api('/tenants/me');
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>)['X-Tenant-Id']).toBe('tenant-2');
  });

  it('creates a workspace and opens it, keeping the others', async () => {
    const user = await openMenu();
    await user.click(await screen.findByRole('menuitem', { name: 'Create workspace' }));
    await user.type(await screen.findByLabelText('Workspace name'), 'Tea House');
    await user.click(screen.getByRole('button', { name: 'Create workspace' }));

    await waitFor(() => expect(sessionStore.getState().user?.tenantId).toBe('tenant-3'));
    expect(sessionStore.getState().user?.workspaces.map((w) => w.tenantId)).toEqual(['tenant-1', 'tenant-2', 'tenant-3']);
    const created = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(created?.[1]?.body as string)).toEqual({ name: 'Tea House' });
  });

  it('sends the same Idempotency-Key when the same workspace is created again, a new one for another name', async () => {
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) =>
      init?.method === 'POST' ? json({ message: 'Busy' }, 503) : answer(input, init),
    );
    const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    const user = await openMenu();
    await user.click(await screen.findByRole('menuitem', { name: 'Create workspace' }));
    await user.type(await screen.findByLabelText('Workspace name'), 'Tea House');
    const submit = screen.getByRole('button', { name: 'Create workspace' });
    await user.click(submit);
    await waitFor(() => expect(posts()).toHaveLength(1));
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(posts()).toHaveLength(2));
    await waitFor(() => expect(submit).toBeEnabled());
    await user.clear(screen.getByLabelText('Workspace name'));
    await user.type(screen.getByLabelText('Workspace name'), 'Leaf Ltd');
    await user.click(submit);
    await waitFor(() => expect(posts()).toHaveLength(3));

    const keys = posts().map(([, init]) => (init?.headers as Record<string, string>)['Idempotency-Key']);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it('offers the invitations the user can accept', async () => {
    invitations = [
      { tenant_id: 'tenant-9', tenant_name: 'Leaf Ltd', email: 'owner@example.com', role: 'member', invited_by: 'x@leaf.test', expires_at: 2_000_000_000 },
    ];
    const user = await openMenu();
    await user.click(await screen.findByRole('menuitem', { name: 'Invitations (1)' }));
    expect(await screen.findByText('“Leaf Ltd”')).toBeInTheDocument();
  });
});
