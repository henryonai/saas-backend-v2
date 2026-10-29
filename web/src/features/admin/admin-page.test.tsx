import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { json, OWNER, renderWithApp } from '@/test/render';
import { AdminPage } from './admin-page';

vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return { ...actual, getIdToken: vi.fn().mockResolvedValue('id-token') };
});

const fetchMock = vi.fn<typeof fetch>();
const acme = { tenant_id: 't1', name: 'Acme', owner_id: 'alice', created_at: '2026-10-01T00:00:00Z', plan: 'free', subscription_status: 'active', comped: false, status: 'active', members: 3, orders: 12 };

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (input) => {
    const path = new URL(String(input), 'http://app.test').pathname;
    if (path === '/admin/tenants') return json({ items: [acme], next_cursor: null });
    if (path === '/admin/log') return json({ items: [], next_cursor: null });
    if (path.startsWith('/admin/tenants/t1/')) return new Response(null, { status: 202 });
    return json({ message: 'not found' }, 404);
  });
});

describe('AdminPage', () => {
  it('lists every workspace and suspends one with a reason', async () => {
    const user = userEvent.setup();
    await renderWithApp(<AdminPage />, { user: { ...OWNER, platformAdmin: true } });
    expect(await screen.findByText('Acme')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Actions for Acme' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Suspend' }));
    const submit = screen.getByRole('button', { name: 'Suspend' });
    expect(submit).toBeDisabled();                                   // a reason first
    await user.type(screen.getByLabelText('Reason'), 'Spam');
    await user.click(submit);

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/admin/tenants/t1/suspend'))).toBe(true));
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/suspend'));
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ reason: 'Spam' });
  });

  it('is closed to everyone else', async () => {
    await renderWithApp(<AdminPage />);
    expect(await screen.findByText(/Only platform admins/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
