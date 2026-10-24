import type { Message } from '@ag-ui/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { routeTree } from '@/routeTree.gen';
import type { AssistantConversation } from '@/shared/api/types';
import { refreshSession, sessionStore } from '@/shared/auth/session';
import { TooltipProvider } from '@/shared/components/ui/tooltip';
import { json, OWNER } from '@/test/render';
import { newConversation } from './conversation';

vi.mock('@/shared/config', () => ({
  config: { apiUrl: 'https://api.test', assistantUrl: 'https://agent.test/invocations', userPoolId: 'pool', userPoolClientId: 'client' },
  missingConfig: () => [],
}));
vi.mock('@/shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
  refreshSession: vi.fn(),
}));

type Event = Record<string, unknown>;
type Ids = { threadId: string; runId: string };
type Answer = (ids: Ids) => { events: Event[]; status?: number };
interface Run {
  threadId: string;
  messages: { content: string }[];
}

/**
 * The two backends: AgentCore (the chat, AG-UI over SSE), answering each run
 * with the next scripted answer, and the HTTP API, keeping conversations as
 * the server does (the first message is the title until the answer ends).
 */
function backend(...answers: Answer[]) {
  const runs: Run[] = [];
  const calls: string[] = [];
  const conversations = new Map<string, AssistantConversation>();
  const histories = new Map<string, Message[]>();

  function keep(threadId: string, title: string, messages: Message[]) {
    const now = new Date().toISOString();
    conversations.set(threadId, { thread_id: threadId, title, created_at: now, updated_at: now, message_count: messages.length });
    histories.set(threadId, messages);
  }

  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (url.startsWith('https://agent.test')) {
      const run: Run & Ids = JSON.parse(String(init?.body));
      runs.push(run);
      const known = conversations.get(run.threadId);
      if (!known) keep(run.threadId, run.messages[0].content, []);
      const { events, status = 200 } = answers[runs.length - 1]({ threadId: run.threadId, runId: run.runId });
      return new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(''), { status, headers: { 'Content-Type': 'text/event-stream' } });
    }
    const path = new URL(url).pathname;
    calls.push(`${method} ${path}`);
    const id = decodeURIComponent(path.split('/assistant/conversations/')[1] ?? '');
    if (path === '/tenants/me') return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', plan: 'free' });
    if (path === '/notifications') return json([]);
    if (path === '/assistant/conversations') {
      return json({ items: [...conversations.values()].reverse(), next_cursor: null });
    }
    const conversation = conversations.get(id);
    if (!conversation) return json({ message: 'No such conversation: it was deleted, or it expired.' }, 404);
    if (method === 'GET') return json({ ...conversation, messages: histories.get(id) });
    if (method === 'PATCH') {
      conversation.title = JSON.parse(String(init?.body)).title;
      return json(conversation);
    }
    if (method === 'DELETE') {
      conversations.delete(id);
      return new Response(null, { status: 204 });
    }
    return json({ message: 'Not Found' }, 404);
  }));
  return { runs, calls, conversations, keep };
}

const finished = (...events: Event[]): Answer => (ids) => ({
  events: [{ type: 'RUN_STARTED', ...ids }, ...events, { type: 'RUN_FINISHED', ...ids, usage: [{ inputTokens: 1200, outputTokens: 34, totalTokens: 1234 }] }],
});
const failed = (code: string, message: string, status = 200): Answer => (ids) => ({
  events: status === 200 ? [{ type: 'RUN_STARTED', ...ids }, { type: 'RUN_ERROR', code, message }] : [{ type: 'RUN_ERROR', code, message }],
  status,
});
const text = (id: string, ...deltas: string[]) => [
  { type: 'TEXT_MESSAGE_START', messageId: id, role: 'assistant' },
  ...deltas.map((delta) => ({ type: 'TEXT_MESSAGE_CONTENT', messageId: id, delta })),
  { type: 'TEXT_MESSAGE_END', messageId: id },
];

const THREAD = '6f3a3e2b-0f6c-4b0a-9a51-1d1e7c11aa01';

function renderAt(path: string) {
  sessionStore.setState({ status: 'signed-in', user: OWNER, reason: null });
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </TooltipProvider>,
  );
  return router;
}

async function ask(question: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByRole('textbox', { name: 'Message the assistant' }), `${question}{Enter}`);
  return user;
}

/** The desktop list (the phone's side panel has a second one, closed). */
async function conversationList() {
  return within((await screen.findAllByRole('navigation', { name: 'Conversations' }))[0]);
}

beforeEach(() => {
  newConversation();
  vi.mocked(refreshSession).mockReset().mockResolvedValue(OWNER);
});

describe('chatting', () => {
  it('streams the answer, its tool calls and their results, and gives the conversation a URL and a place in the list', async () => {
    const server = backend(
      finished(
        ...text('m1', 'Let me ', 'check.'),
        { type: 'TOOL_CALL_START', toolCallId: 'c1', toolCallName: 'list_orders', parentMessageId: 'm1' },
        { type: 'TOOL_CALL_ARGS', toolCallId: 'c1', delta: '{"limit": 5}' },
        { type: 'TOOL_CALL_END', toolCallId: 'c1' },
        { type: 'TOOL_CALL_RESULT', messageId: 't1', toolCallId: 'c1', content: '{"items": [{"item": "Green tea"}]}', role: 'tool' },
        ...text('m2', 'You have **one** order: Green tea.'),
      ),
    );
    const router = renderAt('/assistant');
    await ask('What did we order?');

    expect(await screen.findByText('one', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('Let me check.')).toBeInTheDocument();
    expect(screen.getByText('List orders')).toBeInTheDocument();
    expect(screen.getByText(/"item": "Green tea"/)).toBeInTheDocument();
    expect(await screen.findByText('Last answer: 1,234 tokens')).toBeInTheDocument();
    const [run] = server.runs;
    expect(run.messages).toEqual([expect.objectContaining({ content: 'What did we order?' })]);
    expect(router.state.location.pathname).toBe(`/assistant/${run.threadId}`);
    expect(await (await conversationList()).findByText('What did we order?')).toBeInTheDocument();
  });

  it('continues the conversation: same threadId, only the new message sent', async () => {
    const server = backend(finished(...text('m1', 'Hello!')), finished(...text('m2', 'Still here.')));
    renderAt('/assistant');
    await ask('Hi');
    await screen.findByText('Hello!');
    await ask('Are you there?');
    await screen.findByText('Still here.');

    expect(server.runs[1].threadId).toBe(server.runs[0].threadId);
    expect(server.runs[1].messages.map((message) => message.content)).toEqual(['Are you there?']);
  });

  it("offers the upgrade when the plan's allowance is used", async () => {
    backend(failed('PLAN_LIMIT_REACHED', "The Free plan's 50 assistant messages a month are used."));
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText("Your plan's limit is reached")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See plans' })).toBeInTheDocument();
  });

  it('gets new tokens and asks again once when AgentCore turns the token down', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {}); // the client logs the HTTP error
    const server = backend(failed('ACCESS_DENIED', 'OAuth authorization failed: Token expired', 403), finished(...text('m1', 'Hello!')));
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(server.runs).toHaveLength(2);
  });

  it('says when the assistant failed, and tries again on request', async () => {
    const server = backend(failed('INTERNAL_ERROR', 'The assistant could not answer. Please try again.'), finished(...text('m1', 'Hello!')));
    renderAt('/assistant');
    const user = await ask('Hi');

    expect(await screen.findByText("Something went wrong on the assistant's side. Please try again.")).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    expect(server.runs[1].messages.map((message) => message.content)).toEqual(['Hi']);
  });

  it('starts a new conversation with a new threadId', async () => {
    const server = backend(finished(...text('m1', 'Hello!')), finished(...text('m2', 'Hi again.')));
    const router = renderAt('/assistant');
    const user = await ask('Hi');
    await screen.findByText('Hello!');

    await user.click(screen.getByRole('link', { name: 'New conversation' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/assistant'));
    expect(screen.queryByText('Hello!')).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'What were my last 5 orders?' }));
    await screen.findByText('Hi again.');

    expect(server.runs[1].threadId).not.toBe(server.runs[0].threadId);
  });
});

describe('conversations', () => {
  const history: Message[] = [
    { id: 'u1', role: 'user', content: 'What did we order?' },
    { id: 'a1', role: 'assistant', content: 'Let me check.', toolCalls: [{ id: 'c1', type: 'function', function: { name: 'list_orders', arguments: '{"limit": 5}' } }] },
    { id: 't1', role: 'tool', toolCallId: 'c1', content: '{"items": []}' },
    { id: 'a2', role: 'assistant', content: 'You have no orders yet.' },
  ];

  it('reopens one from the list, as it was, and continues it', async () => {
    const server = backend(finished(...text('m3', 'Still none.')));
    server.keep(THREAD, 'Order check', history);
    const router = renderAt('/assistant');
    const user = userEvent.setup();

    await user.click(await (await conversationList()).findByRole('link', { name: /Order check/ }));

    expect(await screen.findByText('You have no orders yet.')).toBeInTheDocument();
    expect(screen.getByText('List orders')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/assistant/${THREAD}`);
    await ask('And now?');
    await screen.findByText('Still none.');
    expect(server.runs[0].threadId).toBe(THREAD);
    expect(server.runs[0].messages.map((message) => message.content)).toEqual(['And now?']);
  });

  it('opens one from its URL, as after a reload', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history);
    renderAt(`/assistant/${THREAD}`);

    expect(await screen.findByText('You have no orders yet.')).toBeInTheDocument();
    expect(server.calls).toContain(`GET /assistant/conversations/${THREAD}`);
  });

  it('reads a conversation again each time it is opened, so it has the newest messages', async () => {
    const server = backend();
    const OTHER = '6f3a3e2b-0f6c-4b0a-9a51-1d1e7c11aa02';
    server.keep(THREAD, 'Order check', history);
    server.keep(OTHER, 'Something else', [{ id: 'u9', role: 'user', content: 'Another question' }]);
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');

    await user.click((await conversationList()).getByRole('link', { name: /Something else/ }));
    await screen.findByText('Another question');
    server.keep(THREAD, 'Order check', [...history, { id: 'a3', role: 'assistant', content: 'A newer answer.' }]);
    await user.click((await conversationList()).getByRole('link', { name: /Order check/ }));

    expect(await screen.findByText('A newer answer.')).toBeInTheDocument();
  });

  it("says when a conversation isn't there any more", async () => {
    backend();
    renderAt(`/assistant/${THREAD}`);
    expect(await screen.findByText("This conversation isn't available")).toBeInTheDocument();
  });

  it('renames one', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history);
    renderAt('/assistant');
    const user = userEvent.setup();

    await user.click(await (await conversationList()).findByRole('button', { name: 'Options for “Order check”' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Rename' }));
    const input = await screen.findByRole('textbox', { name: 'Title' });
    await user.clear(input);
    await user.type(input, 'Q3 orders{Enter}');

    expect(await (await conversationList()).findByText('Q3 orders')).toBeInTheDocument();
    expect(server.conversations.get(THREAD)?.title).toBe('Q3 orders');
  });

  it('deletes the one on screen, and starts a new one', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history);
    const router = renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');

    await user.click((await conversationList()).getByRole('button', { name: 'Options for “Order check”' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/assistant'));
    expect(server.calls).toContain(`DELETE /assistant/conversations/${THREAD}`);
    expect((await conversationList()).queryByText('Order check')).not.toBeInTheDocument();
    expect(await screen.findByText('Ask the assistant')).toBeInTheDocument();
  });
});
