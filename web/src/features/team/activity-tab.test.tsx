import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { json, renderWithApp } from '@/test/render';
import { ActivityTab } from './components/activity-tab';

vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return { ...actual, getIdToken: vi.fn().mockResolvedValue('id-token') };
});

const fetchMock = vi.fn<typeof fetch>();
const entry = (event_id: string, type: string, details: Record<string, unknown>, actor_email = 'alice@example.com') => ({
  event_id, tenant_id: 'tenant-1', type, occurred_at: '2026-10-28T10:00:00Z', actor_id: 'alice', actor_email, details,
});

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://app.test');
    if (url.searchParams.get('cursor') === 'next') return json({ items: [entry('e3', 'TenantCreated', { name: 'Acme' })], next_cursor: null });
    return json({
      items: [
        entry('e1', 'RoleChanged', { email: 'bob@example.com', role: 'admin', user_id: 'bob' }),
        entry('e2', 'SomethingNew', {}, ''),
      ],
      next_cursor: 'next',
    });
  });
});

describe('ActivityTab', () => {
  it('says who did what, in words, and loads older entries', async () => {
    const user = userEvent.setup();
    await renderWithApp(<ActivityTab />);
    expect(await screen.findByText('Made bob@example.com Admin')).toBeInTheDocument();
    expect(screen.getByText('SomethingNew')).toBeInTheDocument();   // a type the app doesn't know yet
    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Created the workspace Acme')).toBeInTheDocument();
  });
});
