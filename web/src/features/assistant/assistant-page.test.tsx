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
  forwardedProps: { agent?: string; tenantId?: string; resume?: { after: number; startedAt: number } };
}
interface Sent {
  id: string;
  content: string;
  agent: string;
  replaces?: string;
  attachments?: string[];
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
  const ratings = new Map<string, Record<string, string>>();
  const rated: { threadId: string; messageId: string; rating: string | null }[] = [];
  const running = new Map<string, string>();
  const refusals: { status: number; message: string }[] = [];
  // A long conversation: the cursor its opening gives, and the page each cursor reads.
  const earlierFrom = new Map<string, string>();
  const earlier = new Map<string, { messages: Message[]; ratings: Record<string, string>; earlier_cursor: string | null }>();
  // Each conversation's messages with other versions, and what showing one of them shows: `${messageId}/${version}`.
  const versions = new Map<string, Record<string, { index: number; count: number }>>();
  const shownVersions = new Map<string, { messages: Message[]; versions: Record<string, { index: number; count: number }> }>();
  // Files: the uploads S3 got, and each file's record.
  const uploads: FormData[] = [];
  const files = new Map<string, Record<string, unknown>>();

  function keep(threadId: string, title: string, messages: Message[]) {
    const now = new Date().toISOString();
    conversations.set(threadId, { thread_id: threadId, title, created_at: now, updated_at: now, message_count: messages.length });
    histories.set(threadId, [...messages]); // a copy: what the tests send changes it
  }

  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (url.startsWith('https://s3.test')) {
      uploads.push(init?.body as FormData);
      return new Response(null, { status: 204 });
    }
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
    if (path === '/assistant/files' && method === 'POST') {
      const { name, content_type, size } = JSON.parse(String(init?.body));
      const file = { file_id: `f${files.size + 1}`, name, content_type, size, kind: content_type.startsWith('image/') ? 'image' : 'text', source: 'upload', status: 'pending', created_at: '', updated_at: '', versions: [], thread_id: null, agent: null };
      files.set(file.file_id, file);
      return json({ file, url: 'https://s3.test/', fields: { key: `t/u/${file.file_id}/1` } }, 201);
    }
    if (path.startsWith('/assistant/files/')) {
      const fileId = path.split('/')[3];
      return json({ ...files.get(fileId), file_id: fileId, download_url: `https://s3.test/download/${fileId}`, view_url: `https://s3.test/view/${fileId}` });
    }
    if (path === '/assistant/conversations') {
      return json({ items: [...conversations.values()].reverse(), next_cursor: null });
    }
    const [rawId, action, rawMessageId] = (path.split('/assistant/conversations/')[1] ?? '').split('/');
    const id = decodeURIComponent(rawId);
    if (method === 'POST' && action === 'messages' && !rawMessageId) {
      const message: Sent = JSON.parse(String(init?.body));
      sent.push(message);
      const refusal = refusals.shift();
      if (refusal) return json({ message: refusal.message }, refusal.status);
      if (!conversations.has(id)) keep(id, message.content, []);
      const messages = histories.get(id)!;
      // An edit, or the same message again: the new version is shown in the place of that one and what came after it.
      const at = messages.findIndex((m) => m.id === (message.replaces ?? message.id));
      if (at >= 0) messages.splice(at);
      messages.push({ id: message.id, role: 'user', content: message.content });
      if (message.replaces && message.replaces !== message.id) {
        const count = (versions.get(id)?.[message.replaces]?.count ?? 1) + 1;
        versions.set(id, { ...versions.get(id), [message.id]: { index: count, count } });
      }
      running.set(id, `run-${sent.length}`);
      return json({ run_id: running.get(id) }, 201);
    }
    if (method === 'POST' && action === 'messages' && path.endsWith('/version')) {
      const { version } = JSON.parse(String(init?.body));
      const shown = shownVersions.get(`${decodeURIComponent(rawMessageId)}/${version}`);
      if (!shown) return json({ message: 'That message has no such version.' }, 404);
      histories.set(id, [...shown.messages]);
      versions.set(id, shown.versions);
      return json({ ...conversations.get(id), run_id: null, messages: shown.messages, ratings: {}, versions: shown.versions, earlier_cursor: null });
    }
    if (method === 'PATCH' && action === 'messages') {
      const { rating } = JSON.parse(String(init?.body));
      rated.push({ threadId: id, messageId: decodeURIComponent(rawMessageId), rating });
      return new Response(null, { status: 204 });
    }
    if (method === 'POST' && action === 'runs') return new Response(null, { status: 204 });
    if (method === 'GET' && action === 'messages') return json(earlier.get(new URL(url).searchParams.get('before') ?? ''));
    const conversation = conversations.get(id);
    if (!conversation) return json({ message: 'No such conversation: it was deleted, or it expired.' }, 404);
    if (method === 'GET') {
      return json({
        ...conversation, run_id: running.get(id) ?? null, messages: histories.get(id), ratings: ratings.get(id) ?? {},
        earlier_cursor: earlierFrom.get(id) ?? null, versions: versions.get(id) ?? {},
      });
    }
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
  return {
    runs, sent, calls, conversations, histories, running, keep, ratings, rated, earlierFrom, earlier, versions, shownVersions, uploads, files,
    refuse: (status: number, message: string) => refusals.push({ status, message }),
  };
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
    expect(run.forwardedProps.tenantId).toBe('tenant-1'); // the workspace this tab works in
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

  it('reads on from where it was when the stream drops, as AgentCore still has the run', async () => {
    const dropped: Answer = (ids) => ({ events: [{ type: 'RUN_STARTED', ...ids, timestamp: 1700 }, ...text('m1', 'Once upon').slice(0, 2)], drop: true });
    // The rest: RUN_STARTED again, the open message started again, then what the page didn't have.
    const rest = finished(...text('m1', ' a time.'));
    const server = backend(dropped, (ids) => {
      const answer = rest(ids);
      answer.events[0] = { ...answer.events[0], timestamp: 1700 };
      return answer;
    });
    renderAt('/assistant');
    await ask('Tell me a story');

    expect(await screen.findByText('Once upon a time.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(/Once upon/)).toHaveLength(1);
    expect(server.runs[1].forwardedProps.resume).toEqual({ after: 3, startedAt: 1700 });
  });

  it('draws the run from the start when AgentCore answers with another telling of it', async () => {
    const dropped: Answer = (ids) => ({ events: [{ type: 'RUN_STARTED', ...ids, timestamp: 1700 }, ...text('m1', 'Once upon').slice(0, 2)], drop: true });
    const again = finished(...text('m2', 'Long ago, a story.'));
    backend(dropped, (ids) => {
      const answer = again(ids);
      answer.events[0] = { ...answer.events[0], timestamp: 1900 }; // started again, in another microVM
      return answer;
    });
    renderAt('/assistant');
    await ask('Tell me a story');

    expect(await screen.findByText('Long ago, a story.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/Once upon/)).not.toBeInTheDocument();
  });

  it('says when the user sent the hourly number of messages, with no Try again', async () => {
    const server = backend();
    server.refuse(429, 'You can send the assistant 20 messages an hour. Try again in 12 minutes.');
    renderAt('/assistant');
    await ask('Hi');

    expect(await screen.findByText('You can send the assistant 20 messages an hour. Try again in 12 minutes.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(server.runs).toEqual([]);
  });

  it('asks for the last answer again in its place', async () => {
    const server = backend(finished(...text('m1', 'Paris.')), finished(...text('m2', "It's Paris.")));
    renderAt('/assistant');
    const user = await ask('Capital of France?');
    await screen.findByText('Paris.');

    await user.click(screen.getByRole('button', { name: 'Regenerate' }));

    expect(await screen.findByText("It's Paris.")).toBeInTheDocument();
    expect(screen.queryByText('Paris.')).not.toBeInTheDocument();
    // The same message, answered again: on the server the old answer stays, a version before this one.
    expect(server.sent[1]).toEqual({ ...server.sent[0], replaces: server.sent[0].id });
    expect(screen.getAllByRole('button', { name: 'Edit message' })).toHaveLength(1); // one question on screen
  });

  it('takes back what it drew when the stream drops, and draws the run once from the start', async () => {
    const dropped: Answer = (ids) => ({ events: [{ type: 'RUN_STARTED', ...ids }, ...text('m1', 'Once upon').slice(0, 2)], drop: true });
    backend(dropped, finished(...text('m1', 'Once upon', ' a time.')));
    renderAt('/assistant');
    await ask('Tell me a story');

    expect(await screen.findByText('Once upon a time.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(/Once upon/)).toHaveLength(1);
  });

  it('attaches a file: uploaded to S3 at once, then sent with the message, which shows it', async () => {
    const server = backend(finished(...text('m1', 'Buy green tea.')));
    renderAt('/assistant');
    const user = userEvent.setup();
    await user.upload(await screen.findByLabelText('Files to attach'), new File(['Buy more green tea.'], 'notes.txt', { type: 'text/plain' }));
    const attached = within(await screen.findByRole('list', { name: 'Files attached' }));
    expect(await attached.findByText('notes.txt')).toBeInTheDocument();
    // Once it is up, the file alone could be sent.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
    expect(server.uploads).toHaveLength(1);

    await ask('What do I need?');

    expect(await screen.findByText('Buy green tea.')).toBeInTheDocument();
    expect(server.sent).toEqual([expect.objectContaining({ content: 'What do I need?', attachments: ['f1'] })]);
    expect(server.uploads[0].get('key')).toBe('t/u/f1/1');
    expect(screen.getByRole('button', { name: 'Download notes.txt' })).toBeInTheDocument(); // on the message
    expect(screen.queryByRole('list', { name: 'Files attached' })).not.toBeInTheDocument(); // the composer is clear
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

  it('edits a message: it and what came after make way on screen for the new version and its answer', async () => {
    const server = backend(finished(...text('m5', 'Here are your paid orders.')));
    server.keep(THREAD, 'Order check', history);
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');

    await user.click(screen.getByRole('button', { name: 'Edit message' }));
    const editor = screen.getByRole('textbox', { name: 'Your message' });
    await user.clear(editor);
    await user.type(editor, 'Which orders are paid?{Enter}');

    expect(await screen.findByText('Here are your paid orders.')).toBeInTheDocument();
    expect(screen.queryByText('What did we order?')).not.toBeInTheDocument();
    expect(screen.queryByText('You have no orders yet.')).not.toBeInTheDocument();
    expect(server.sent).toEqual([expect.objectContaining({ content: 'Which orders are paid?', replaces: 'u1' })]);
    expect(server.histories.get(THREAD)?.map((m) => m.content)).toEqual(['Which orders are paid?']);
  });

  it('numbers an edit as the newest version once it is answered, and shows the one before on request', async () => {
    const server = backend(finished(...text('m5', 'Here are your paid orders.')));
    server.keep(THREAD, 'Order check', history);
    server.shownVersions.set(`${'edited'}/1`, { messages: history, versions: { u1: { index: 1, count: 2 } } });
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');

    await user.click(screen.getByRole('button', { name: 'Edit message' }));
    const editor = screen.getByRole('textbox', { name: 'Your message' });
    await user.clear(editor);
    await user.type(editor, 'Which orders are paid?{Enter}');
    await screen.findByText('Here are your paid orders.');

    expect(await screen.findByRole('group', { name: 'Version 2 of 2' })).toBeInTheDocument();
    const edited = server.sent[0].id;
    server.shownVersions.set(`${edited}/1`, { messages: history, versions: { u1: { index: 1, count: 2 } } });
    await user.click(screen.getByRole('button', { name: 'Previous version' }));

    expect(await screen.findByText('You have no orders yet.')).toBeInTheDocument();
    expect(screen.queryByText('Here are your paid orders.')).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Version 1 of 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous version' })).toBeDisabled();
    expect(server.calls).toContain(`POST /assistant/conversations/${THREAD}/messages/${edited}/version`);
  });

  it('shows the versions an answer has, and asks for any answer again', async () => {
    const server = backend(finished(...text('m9', 'Nothing yet, still.')));
    server.keep(THREAD, 'Order check', [...history, { id: 'u2', role: 'user', content: 'And now?' }, { id: 'a3', role: 'assistant', content: 'Still none.' }]);
    server.versions.set(THREAD, { a1: { index: 2, count: 2 } });
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('Still none.');
    expect(screen.getByRole('group', { name: 'Version 2 of 2' })).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: 'Regenerate' })[0]); // the first answer

    expect(await screen.findByText('Nothing yet, still.')).toBeInTheDocument();
    expect(screen.queryByText('Still none.')).not.toBeInTheDocument(); // what came after it makes way
    expect(server.sent).toEqual([expect.objectContaining({ id: 'u1', content: 'What did we order?', replaces: 'u1' })]);
  });

  it('rates an answer, shows the rating when it opens again, and takes it back', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history);
    server.ratings.set(THREAD, { a2: 'down' });
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');
    expect(screen.getByRole('button', { name: 'Bad answer' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Good answer' }));
    expect(screen.getByRole('button', { name: 'Good answer' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Bad answer' })).toHaveAttribute('aria-pressed', 'false');
    await user.click(screen.getByRole('button', { name: 'Good answer' }));

    expect(screen.getByRole('button', { name: 'Good answer' })).toHaveAttribute('aria-pressed', 'false');
    // On the answer's last message (a2), not the first (a1).
    await waitFor(() => expect(server.rated).toEqual([
      { threadId: THREAD, messageId: 'a2', rating: 'up' },
      { threadId: THREAD, messageId: 'a2', rating: null },
    ]));
  });

  it('reads a long conversation back from its latest messages, a page at a time', async () => {
    const server = backend();
    server.keep(THREAD, 'Order check', history.slice(2)); // it opens at its latest messages
    server.earlierFrom.set(THREAD, 'c1');
    server.earlier.set('c1', { messages: history.slice(1, 2), ratings: {}, earlier_cursor: 'c2' });
    server.earlier.set('c2', { messages: history.slice(0, 1), ratings: {}, earlier_cursor: null });
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();
    await screen.findByText('You have no orders yet.');
    expect(screen.queryByText('Let me check.')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show earlier messages' }));
    expect(await screen.findByText('Let me check.')).toBeInTheDocument();
    expect(screen.getByText('List orders')).toBeInTheDocument(); // its result was on screen already
    await user.click(screen.getByRole('button', { name: 'Show earlier messages' }));

    expect(await screen.findByText('What did we order?')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show earlier messages' })).not.toBeInTheDocument();
    expect(server.calls.filter((call) => call === `GET /assistant/conversations/${THREAD}/messages`)).toHaveLength(2);
  });

  it('shows the files on a message, an image as itself, and offers what an agent saved', async () => {
    const server = backend();
    server.files.set('f9', { name: 'receipt.png', kind: 'image' });
    const saved = { file_id: 'f7', name: 'report.md', content_type: 'text/markdown', size: 2048, version: 2 };
    server.keep(THREAD, 'Receipt', [
      { id: 'u1', role: 'user', content: [
        { type: 'text', text: 'Write up this receipt' },
        { type: 'image', source: { type: 'file', value: 'f9', mimeType: 'image/png' }, metadata: { name: 'receipt.png', size: 2048, kind: 'image' } },
      ] } as Message,
      { id: 'a1', role: 'assistant', content: '', toolCalls: [{ id: 'c1', type: 'function', function: { name: 'write_file', arguments: '{"name": "report.md", "content": "# Receipt"}' } }] },
      { id: 't1', role: 'tool', toolCallId: 'c1', content: JSON.stringify(saved) },
      { id: 'a2', role: 'assistant', content: 'Here is the write-up.' },
    ]);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderAt(`/assistant/${THREAD}`);
    const user = userEvent.setup();

    expect(await screen.findByRole('img', { name: 'Image: receipt.png' })).toHaveAttribute('src', 'https://s3.test/view/f9');
    expect(screen.getByText('Write up this receipt')).toBeInTheDocument();
    expect(screen.getByText(/version 2/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Download report.md' }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(server.calls).toContain('GET /assistant/files/f7');
    click.mockRestore();
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
