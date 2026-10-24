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
/** `open`: the stream stays open after the events, until the client aborts it; `drop`: it breaks after them. */
type Answer = (ids: Ids) => { events: Event[]; status?: number; open?: boolean; drop?: boolean };
interface Run extends Ids {
  messages: unknown[];
}
interface Sent {
  id: string;
  content: string;
  agent: string;
}

/**
 * The two backends, as they behave: the HTTP API saves a message with the
 * run that will answer it (or refuses it: `refuse`), and keeps the
 * conversations (the first message is the title); AgentCore answers each run
 * with the next scripted answer. `running`: the run each conversation's
 * answer is coming from.
 */
function backend(...answers: Answer[]) {
  const runs: Run[] = [];
  const sent: Sent[] = [];
  const calls: string[] = [];
  const conversations = new Map<string, AssistantConversation>();
  const histories = new Map<string, Message[]>();
  const running = new Map<string, string>();
  const refusals: { status: number; message: string }[] = [];

  function keep(threadId: string, title: string, messages: Message[]) {
    const now = new Date().toISOString();
    conversations.set(threadId, { thread_id: threadId, title, created_at: now, updated_at: now, message_count: messages.length });
    histories.set(threadId, messages);
  }

  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (url.startsWith('https://agent.test')) {
      const run: Run = JSON.parse(String(init?.body));
      runs.push(run);
      const { events, status = 200, open = false, drop = false } = answers[runs.length - 1]({ threadId: run.threadId, runId: run.runId });
      if (!open && !drop && status < 500) running.delete(run.threadId);
      const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
      const headers = { 'Content-Type': 'text/event-stream' };
      if (!open && !drop) return new Response(body, { status, headers });
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body));
          if (drop) setTimeout(() => controller.error(new TypeError('network error')), 10);
          init?.signal?.addEventListener('abort', () => controller.error(new DOMException('The user aborted a request.', 'AbortError')));
        },
      });
      return new Response(stream, { status, headers });
    }
    const path = new URL(url).pathname;
    calls.push(`${method} ${path}`);
    if (path === '/tenants/me') return json({ tenant_id: 'tenant-1', name: 'Acme Coffee', plan: 'free' });
    if (path === '/notifications') return json([]);
    if (path === '/assistant/conversations') {
      return json({ items: [...conversations.values()].reverse(), next_cursor: null });
    }
    const [rawId, action] = (path.split('/assistant/conversations/')[1] ?? '').split('/');
    const id = decodeURIComponent(rawId);
    if (method === 'POST' && action === 'messages') {
      const message: Sent = JSON.parse(String(init?.body));
      sent.push(message);
      const refusal = refusals.shift();
      if (refusal) return json({ message: refusal.message }, refusal.status);
      if (!conversations.has(id)) keep(id, message.content, []);
      const messages = histories.get(id)!;
      if (messages.at(-1)?.id !== message.id) messages.push({ id: message.id, role: 'user', content: message.content });
      running.set(id, `run-${sent.length}`);
      return json({ run_id: running.get(id) }, 201);
    }
    if (method === 'POST' && action === 'runs') return new Response(null, { status: 204 });
    const conversation = conversations.get(id);
    if (!conversation) return json({ message: 'No such conversation: it was deleted, or it expired.' }, 404);
    if (method === 'GET') return json({ ...conversation, run_id: running.get(id) ?? null, messages: histories.get(id) });
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
  return { runs, sent, calls, conversations, running, keep, refuse: (status: number, message: string) => refusals.push({ status, message }) };
}

const finished = (...events: Event[]): Answer => (ids) => ({
  events: [{ type: 'RUN_STARTED', ...ids }, ...events, { type: 'RUN_FINISHED', ...ids, usage: [{ inputTokens: 1200, outputTokens: 34, totalTokens: 1234 }] }],
});
/** Still streaming when the test acts. */
const streaming = (...events: Event[]): Answer => (ids) => ({ events: [{ type: 'RUN_STARTED', ...ids }, ...events], open: true });
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

const history: Message[] = [
  { id: 'u1', role: 'user', content: 'What did we order?' },
  { id: 'a1', role: 'assistant', content: 'Let me check.', toolCalls: [{ id: 'c1', type: 'function', function: { name: 'list_orders', arguments: '{"limit": 5}' } }] },
  { id: 't1', role: 'tool', toolCallId: 'c1', content: '{"items": []}' },
  { id: 'a2', role: 'assistant', content: 'You have no orders yet.' },
];

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
  it('saves the message, streams its run, and gives the conversation a URL and a place in the list', async () => {
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
    // The HTTP API saved the message first; AgentCore got only the run it gave.
    expect(server.sent).toEqual([expect.objectContaining({ content: 'What did we order?', agent: 'assistant' })]);
    const [run] = server.runs;
    expect(run).toMatchObject({ runId: 'run-1', messages: [] });
    expect(router.state.location.pathname).toBe(`/assistant/${run.threadId}`);
    expect(await (await conversationList()).findByText('What did we order?')).toBeInTheDocument();
  });

  it('continues the conversation: same threadId, the new message saved', async () => {
    const server = backend(finished(...text('m1', 'Hello!')), finished(...text('m2', 'Still here.')));
    renderAt('/assistant');
    await ask('Hi');
    await screen.findByText('Hello!');
    await ask('Are you there?');
    await screen.findByText('Still here.');

    expect(server.runs[1]).toMatchObject({ threadId: server.runs[0].threadId, runId: 'run-2' });
    expect(server.sent.map((message) => message.content)).toEqual(['Hi', 'Are you there?']);
  });

  it("offers the upgrade when the plan's allowance is used, before anything runs", async () => {
    const server = backend();
    server.refuse(402, "The Free plan's 50 assistant messages a month are used.");
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText("Your plan's limit is reached")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See plans' })).toBeInTheDocument();
    expect(server.runs).toEqual([]);
  });

  it('says when the previous answer is still coming (from another tab)', async () => {
    const server = backend();
    server.refuse(409, 'The assistant is still answering in this conversation: wait for the answer, or stop it.');
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText(/still answering in this conversation/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('gets new tokens and asks again once when AgentCore turns the token down', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {}); // the client logs the HTTP error
    const server = backend(failed('ACCESS_DENIED', 'OAuth authorization failed: Token expired', 403), finished(...text('m1', 'Hello!')));
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(server.sent).toHaveLength(1);
    expect(server.runs.map((run) => run.runId)).toEqual(['run-1', 'run-1']);
  });

  it('says when the assistant failed, and tries again on request', async () => {
    const server = backend(failed('INTERNAL_ERROR', 'The assistant could not answer. Please try again.'), finished(...text('m1', 'Hello!')));
    renderAt('/assistant');
    const user = await ask('Hi');

    expect(await screen.findByText("Something went wrong on the assistant's side. Please try again.")).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    // The same message again (the server doesn't save it twice), with a new run.
    expect(server.sent[1]).toEqual(server.sent[0]);
    expect(server.runs[1].runId).toBe('run-2');
  });

  it('follows the run again when AgentCore fails to reach it at first, as a session still starting does', async () => {
    // AgentCore's own error, in a 200 stream; then one with an HTTP status.
    const unreachable: Answer = () => ({ events: [{ type: 'RUN_ERROR', code: 'AGENT_ERROR', message: 'Received error (502) from runtime.' }] });
    const server = backend(unreachable, failed('INTERNAL_ERROR', 'Bad gateway', 502), finished(...text('m1', 'Hello!')));
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText('Hello!', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText("The assistant couldn't answer")).not.toBeInTheDocument();
    expect(server.sent).toHaveLength(1); // the message once; its run asked for three times
    expect(server.runs.map((run) => run.runId)).toEqual(['run-1', 'run-1', 'run-1']);
  });

  it('takes back what it drew when the stream drops, and draws the run once from the start', async () => {
    const dropped: Answer = (ids) => ({ events: [{ type: 'RUN_STARTED', ...ids }, ...text('m1', 'Once upon').slice(0, 2)], drop: true });
    backend(dropped, finished(...text('m1', 'Once upon', ' a time.')));
    renderAt('/assistant');
    await ask('Tell me a story');

    expect(await screen.findByText('Once upon a time.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(/Once upon/)).toHaveLength(1);
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

  it('stops the answer on the server too on Stop, which is no error', async () => {
    const server = backend(streaming(...text('m1', 'Once upon a time').slice(0, 2)));
    renderAt('/assistant');
    const user = await ask('Tell me a story');
    await screen.findByText('Once upon a time');

    await user.click(screen.getByRole('button', { name: 'Stop' }));

    expect(await screen.findByRole('button', { name: 'Send' })).toBeInTheDocument();
    expect(screen.getByText('Once upon a time')).toBeInTheDocument();
    expect(screen.queryByText("The assistant couldn't answer")).not.toBeInTheDocument();
    expect(server.calls).toContain(`POST /assistant/conversations/${server.runs[0].threadId}/runs/run-1/cancel`);
  });
});

describe('conversations', () => {
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
    expect(server.sent.map((message) => message.content)).toEqual(['And now?']);
  });

  it('opens one from its URL, as after a reload', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history);
    renderAt(`/assistant/${THREAD}`);

    expect(await screen.findByText('You have no orders yet.')).toBeInTheDocument();
    expect(server.calls).toContain(`GET /assistant/conversations/${THREAD}`);
    expect(server.runs).toEqual([]); // nothing coming: nothing to follow
  });

  it('follows an answer still coming when it opens, from the start, as after a reload mid-answer', async () => {
    const server = backend(streaming(...text('m9', 'You have no ').slice(0, 2)));
    server.keep(THREAD, 'Order check', history.slice(0, 1));
    server.running.set(THREAD, 'run-7');
    renderAt(`/assistant/${THREAD}`);

    expect(await screen.findByText('You have no')).toBeInTheDocument();
    expect(screen.getByText('What did we order?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    expect(server.runs).toEqual([expect.objectContaining({ threadId: THREAD, runId: 'run-7' })]);
    expect(server.sent).toEqual([]); // nothing sent again
  });

  it('shows the history when the answer ended before the page asked for it', async () => {
    const server = backend((ids) => {
      server.keep(THREAD, 'Order check', history); // it ended meanwhile, and AgentCore let it go
      return failed('NOT_FOUND', 'No such run in this conversation')(ids);
    });
    server.keep(THREAD, 'Order check', history.slice(0, 1));
    server.running.set(THREAD, 'run-7');
    renderAt(`/assistant/${THREAD}`);

    expect(await screen.findByText('You have no orders yet.')).toBeInTheDocument();
    expect(screen.queryByText("The assistant couldn't answer")).not.toBeInTheDocument();
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
