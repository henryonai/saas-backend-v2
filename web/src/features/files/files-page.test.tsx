import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { routeTree } from '@/routeTree.gen';
import type { AssistantFile } from '@/shared/api/types';
import { sessionStore } from '@/shared/auth/session';
import { TooltipProvider } from '@/shared/components/ui/tooltip';
import { json, OWNER } from '@/test/render';

vi.mock('@/shared/config', () => ({
  config: { apiUrl: 'https://api.test', assistantUrl: 'https://agent.test/invocations', userPoolId: 'pool', userPoolClientId: 'client' },
  missingConfig: () => [],
}));
vi.mock('@/shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
}));

const THREAD = '6f3a3e2b-0f6c-4b0a-9a51-1d1e7c11aa01';

function file(file_id: string, name: string, changes: Partial<AssistantFile> = {}): AssistantFile {
  return {
    file_id, name, content_type: 'text/markdown', kind: 'text', size: 2048, source: 'assistant', status: 'ready',
    created_at: '2026-10-26T08:00:00.000Z', updated_at: '2026-10-26T08:00:00.000Z',
    versions: [{ version: 1, size: 2048, created_at: '2026-10-26T08:00:00.000Z' }], thread_id: THREAD, agent: 'analyst', ...changes,
  };
}

/** /assistant/files as the HTTP API answers it, filtering by source and kind. */
function backend(items: AssistantFile[]) {
  let files = [...items];
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const { pathname, searchParams } = new URL(url);
    calls.push(`${method} ${pathname}${searchParams.size ? `?${searchParams}` : ''}`);
    if (pathname === '/tenants/me') return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', plan: 'free' });
    if (pathname === '/notifications') return json([]);
    if (pathname === '/assistant/files') {
      const source = searchParams.get('source');
      const kind = searchParams.get('kind');
      return json({ items: files.filter((f) => (!source || f.source === source) && (!kind || f.kind === kind)), next_cursor: null });
    }
    const fileId = pathname.split('/')[3];
    if (method === 'DELETE') {
      files = files.filter((f) => f.file_id !== fileId);
      return new Response(null, { status: 204 });
    }
    return json({ ...files.find((f) => f.file_id === fileId), download_url: `https://s3.test/${fileId}`, view_url: null });
  }));
  return { calls };
}

function renderFiles() {
  sessionStore.setState({ status: 'signed-in', user: OWNER, reason: null });
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ['/files'] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </TooltipProvider>,
  );
}

describe('files', () => {
  it('lists what the assistant made and what the user attached, by source, and links each to its conversation', async () => {
    const server = backend([
      file('f2', 'report.md', { versions: [1, 2, 3].map((version) => ({ version, size: 2048, created_at: '' })) }),
      file('f1', 'receipt.png', { kind: 'image', content_type: 'image/png', source: 'upload', agent: null }),
    ]);
    renderFiles();
    const user = userEvent.setup();

    const table = within(await screen.findByRole('table'));
    expect(table.getByText('report.md')).toBeInTheDocument();
    expect(table.getByText('3 versions')).toBeInTheDocument();
    expect(table.getByText('analyst')).toBeInTheDocument();
    expect(table.getAllByRole('link', { name: 'Open the conversation' })[0]).toHaveAttribute('href', `/assistant/${THREAD}`);

    await user.click(screen.getByRole('button', { name: 'Attached by you' }));
    await waitFor(() => expect(within(screen.getByRole('table')).queryByText('report.md')).not.toBeInTheDocument());
    expect(within(screen.getByRole('table')).getByText('receipt.png')).toBeInTheDocument();
    expect(server.calls).toContain('GET /assistant/files?source=upload&limit=30');
  });

  it('deletes a file after asking', async () => {
    const server = backend([file('f2', 'report.md')]);
    renderFiles();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Delete report.md' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText('No files yet')).toBeInTheDocument();
    expect(server.calls).toContain('DELETE /assistant/files/f2');
  });
});
