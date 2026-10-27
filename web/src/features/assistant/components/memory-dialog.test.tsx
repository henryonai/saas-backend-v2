import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AssistantMemory } from '@/shared/api/types';
import { json, renderWithApp } from '@/test/render';
import { MemoryButton } from './memory-dialog';

vi.mock('@/shared/config', () => ({
  config: { apiUrl: 'https://api.test', assistantUrl: 'https://agent.test/invocations', userPoolId: 'pool', userPoolClientId: 'client' },
  missingConfig: () => [],
}));
vi.mock('@/shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
}));

/** /assistant/memories as the HTTP API keeps them. */
function backend(...texts: string[]) {
  let enabled = true;
  let memories: AssistantMemory[] = texts.map((text, n) => ({
    memory_id: `m${n + 1}`, text, created_at: '2026-10-26T08:00:00.000Z', updated_at: '2026-10-26T08:00:00.000Z', thread_id: null,
  }));
  const calls: string[] = [];
  const list = () => json({ enabled, items: memories, limit: 100 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = new URL(url).pathname;
    calls.push(`${method} ${path}`);
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    const id = path.split('/assistant/memories/')[1];
    if (path === '/assistant/memories') {
      if (method === 'POST') {
        const memory = { ...memories[0], memory_id: `m${memories.length + 1}`, text: body.text };
        memories = [...memories, memory];
        return json(memory, 201);
      }
      if (method === 'PATCH') enabled = body.enabled;
      if (method === 'DELETE') {
        memories = [];
        return new Response(null, { status: 204 });
      }
      return list();
    }
    if (method === 'PATCH') {
      memories = memories.map((memory) => (memory.memory_id === id ? { ...memory, text: body.text } : memory));
      return json(memories.find((memory) => memory.memory_id === id));
    }
    memories = memories.filter((memory) => memory.memory_id !== id);
    return new Response(null, { status: 204 });
  }));
  return { calls, enabled: () => enabled };
}

async function open() {
  const user = userEvent.setup();
  await renderWithApp(<MemoryButton />);
  await user.click(screen.getByRole('button', { name: 'Memory' }));
  return { user, dialog: within(await screen.findByRole('dialog')) };
}

beforeEach(() => vi.unstubAllGlobals());

describe('memory', () => {
  it('lists what the assistant remembers, and adds, edits and deletes it', async () => {
    const server = backend('Prefers metric units', 'Works in Hanoi');
    const { user, dialog } = await open();
    const list = within(await dialog.findByRole('list', { name: 'What the assistant remembers' }));
    expect(list.getAllByRole('listitem').map((item) => item.textContent)).toEqual(['Prefers metric units', 'Works in Hanoi']);

    await user.type(dialog.getByRole('textbox', { name: 'Something for the assistant to remember' }), 'Likes short answers{Enter}');
    expect(await list.findByText('Likes short answers')).toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Edit “Works in Hanoi”' }));
    const editor = dialog.getByRole('textbox', { name: 'Edit memory' });
    await user.clear(editor);
    await user.type(editor, 'Works in Hue{Enter}');
    expect(await list.findByText('Works in Hue')).toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Delete “Prefers metric units”' }));
    await waitFor(() => expect(list.queryByText('Prefers metric units')).not.toBeInTheDocument());
    expect(server.calls).toEqual(expect.arrayContaining(['POST /assistant/memories', 'PATCH /assistant/memories/m2', 'DELETE /assistant/memories/m1']));
    expect(dialog.getByText('2 of 100')).toBeInTheDocument();
  });

  it('turns memory off, and forgets everything after asking', async () => {
    const server = backend('Prefers metric units');
    const { user, dialog } = await open();
    const toggle = await dialog.findByRole('switch', { name: /Remember things across conversations/ });
    expect(toggle).toBeChecked();

    await user.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(server.enabled()).toBe(false);
    expect(dialog.getByText(/Off: it neither uses nor saves memories/)).toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Forget everything' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Forget everything' }));
    expect(await screen.findByText(/Nothing remembered yet/)).toBeInTheDocument();
    expect(server.calls).toContain('DELETE /assistant/memories');
  });
});
