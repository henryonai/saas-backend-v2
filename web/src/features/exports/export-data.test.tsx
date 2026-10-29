import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { json, renderWithApp } from '@/test/render';
import { ExportData } from './export-data';

vi.mock('@/shared/auth/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/auth/session')>();
  return { ...actual, getIdToken: vi.fn().mockResolvedValue('id-token') };
});

const fetchMock = vi.fn<typeof fetch>();
const running = { job_id: 'j1', kind: 'user', status: 'running', parts: ['accounts', 'notifications'], done: ['accounts'], created_at: '2026-10-28T00:00:00Z', expires_at: 2_000_000_000, files: {} };
const done = { ...running, status: 'done', done: ['accounts', 'notifications'], files: { accounts: 'https://exports.test/a', notifications: 'https://exports.test/n' } };
let polls = 0;

beforeEach(() => {
  polls = 0;
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://app.test').pathname;
    if (path !== '/exports/me') return json({ message: 'not found' }, 404);
    if (init?.method === 'POST') return json(running, 202);
    polls += 1;
    return json(polls === 1 ? null : done);
  });
});

describe('ExportData', () => {
  it('starts an export, shows its progress, then a link per part', async () => {
    const user = userEvent.setup();
    await renderWithApp(<ExportData kind="me" />);
    await user.click(await screen.findByRole('button', { name: 'Start an export' }));

    expect(await screen.findByText('Preparing the export: 1 of 2 parts ready…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start an export' })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('link', { name: 'Notifications' })).toHaveAttribute('href', 'https://exports.test/n'), { timeout: 5000 });
    expect(screen.getByRole('link', { name: 'Members and workspaces' })).toBeInTheDocument();
  });
});
