import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { refreshSession } from '@/shared/auth/session';
import { json, OWNER, renderWithApp } from '@/test/render';
import { DeleteWorkspace } from './components/delete-workspace';

vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return { ...actual, getIdToken: vi.fn().mockResolvedValue('id-token'), refreshSession: vi.fn().mockResolvedValue(null) };
});

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://app.test').pathname;
    if (path === '/tenants/me' && init?.method === 'DELETE') {
      return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', status: 'deleting', delete_after: 2_000_000_000 });
    }
    if (path === '/tenants/me') return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', plan: 'free', status: 'active' });
    return json({ message: 'not found' }, 404);
  });
});

describe('DeleteWorkspace', () => {
  it('deletes the workspace only once its name is typed, then refreshes the session', async () => {
    const user = userEvent.setup();
    await renderWithApp(<DeleteWorkspace />);
    await user.click(await screen.findByRole('button', { name: 'Delete workspace' }));
    const submit = screen.getAllByRole('button', { name: 'Delete workspace' }).at(-1)!;
    await user.type(screen.getByLabelText('Type Acme Coffee to confirm'), 'acme coffee');
    expect(submit).toBeDisabled();
    await user.clear(screen.getByLabelText('Type Acme Coffee to confirm'));
    await user.type(screen.getByLabelText('Type Acme Coffee to confirm'), 'Acme Coffee');
    await user.click(submit);

    await waitFor(() => expect(refreshSession).toHaveBeenCalled());
    const request = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(JSON.parse(request?.[1]?.body as string)).toEqual({ name: 'Acme Coffee' });
  });

  it('is only offered to the owner', async () => {
    await renderWithApp(<DeleteWorkspace />, { user: { ...OWNER, role: 'admin', workspaces: [{ tenantId: 'tenant-1', role: 'admin', plan: 'free' }] } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Delete workspace' })).not.toBeInTheDocument();
  });
});
