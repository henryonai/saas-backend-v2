import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { refreshSession } from '@/shared/auth/session';
import { OWNER, renderWithApp } from '@/test/render';
import { AssistantPage } from './assistant-page';
import { newConversation } from './conversation';

vi.mock('@/shared/config', () => ({ config: { apiUrl: 'https://api.test', assistantUrl: 'https://agent.test/invocations' } }));
vi.mock('@/shared/auth/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/auth/session')>()),
  getIdToken: vi.fn().mockResolvedValue('id-token'),
  refreshSession: vi.fn(),
}));

type Event = Record<string, unknown>;
interface Request {
  threadId: string;
  runId: string;
  messages: { content: string }[];
}

/** AgentCore's side: each call gets the next scripted answer, as SSE, around RUN_STARTED/RUN_FINISHED. */
function agentCore(...answers: ((ids: { threadId: string; runId: string }) => { events: Event[]; status?: number })[]) {
  const requests: Request[] = [];
  const fetch = vi.fn(async (_url: string, init: RequestInit) => {
    const request: Request = JSON.parse(String(init.body));
    requests.push(request);
    const { events, status = 200 } = answers[requests.length - 1]({ threadId: request.threadId, runId: request.runId });
    const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
    return new Response(body, { status, headers: { 'Content-Type': 'text/event-stream' } });
  });
  vi.stubGlobal('fetch', fetch);
  return requests;
}

const run = (...events: Event[]) => (ids: { threadId: string; runId: string }) => ({
  events: [{ type: 'RUN_STARTED', ...ids }, ...events, { type: 'RUN_FINISHED', ...ids, usage: [{ inputTokens: 1200, outputTokens: 34, totalTokens: 1234 }] }],
});
const failed = (code: string, message: string, status = 200) => (ids: { threadId: string; runId: string }) => ({
  events: status === 200 ? [{ type: 'RUN_STARTED', ...ids }, { type: 'RUN_ERROR', code, message }] : [{ type: 'RUN_ERROR', code, message }],
  status,
});
const text = (id: string, ...deltas: string[]) => [
  { type: 'TEXT_MESSAGE_START', messageId: id, role: 'assistant' },
  ...deltas.map((delta) => ({ type: 'TEXT_MESSAGE_CONTENT', messageId: id, delta })),
  { type: 'TEXT_MESSAGE_END', messageId: id },
];

async function ask(question: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox', { name: 'Message the assistant' }), `${question}{Enter}`);
  return user;
}

beforeEach(() => {
  newConversation();
  vi.mocked(refreshSession).mockReset().mockResolvedValue(OWNER);
});

describe('AssistantPage', () => {
  it('streams the answer, its tool calls and their results', async () => {
    const requests = agentCore(
      run(
        ...text('m1', 'Let me ', 'check.'),
        { type: 'TOOL_CALL_START', toolCallId: 'c1', toolCallName: 'list_orders', parentMessageId: 'm1' },
        { type: 'TOOL_CALL_ARGS', toolCallId: 'c1', delta: '{"limit": 5}' },
        { type: 'TOOL_CALL_END', toolCallId: 'c1' },
        { type: 'TOOL_CALL_RESULT', messageId: 't1', toolCallId: 'c1', content: '{"items": [{"item": "Green tea"}]}', role: 'tool' },
        ...text('m2', 'You have **one** order: Green tea.'),
      ),
    );
    await renderWithApp(<AssistantPage />);
    await ask('What did we order?');

    expect(await screen.findByText('one', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('What did we order?')).toBeInTheDocument();
    expect(screen.getByText('Let me check.')).toBeInTheDocument();
    expect(screen.getByText('List orders')).toBeInTheDocument();
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText(/"item": "Green tea"/)).toBeInTheDocument();
    expect(await screen.findByText('Last answer: 1,234 tokens')).toBeInTheDocument();
    expect(requests[0].messages).toEqual([expect.objectContaining({ content: 'What did we order?' })]);
  });

  it('continues the conversation: same threadId, only the new message sent', async () => {
    const requests = agentCore(run(...text('m1', 'Hello!')), run(...text('m2', 'Still here.')));
    await renderWithApp(<AssistantPage />);
    await ask('Hi');
    await screen.findByText('Hello!');
    await ask('Are you there?');
    await screen.findByText('Still here.');

    expect(requests[1].threadId).toBe(requests[0].threadId);
    expect(requests[1].messages.map((message) => message.content)).toEqual(['Are you there?']);
  });

  it("offers the upgrade when the plan's allowance is used", async () => {
    agentCore(failed('PLAN_LIMIT_REACHED', "The Free plan's 50 assistant messages a month are used."));
    await renderWithApp(<AssistantPage />);
    await ask('Hi');

    expect(await screen.findByText("Your plan's limit is reached")).toBeInTheDocument();
    expect(screen.getByText("The Free plan's 50 assistant messages a month are used.")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See plans' })).toBeInTheDocument();
  });

  it('gets new tokens and asks again once when AgentCore turns the token down', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});   // the client logs the HTTP error
    const requests = agentCore(failed('ACCESS_DENIED', 'OAuth authorization failed: Token expired', 403), run(...text('m1', 'Hello!')));
    await renderWithApp(<AssistantPage />);
    await ask('Hi');

    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(2);
    expect(screen.queryByText("The assistant couldn't answer")).not.toBeInTheDocument();
  });

  it('says when the assistant failed, and tries again on request', async () => {
    const requests = agentCore(failed('INTERNAL_ERROR', 'The assistant could not answer. Please try again.'), run(...text('m1', 'Hello!')));
    await renderWithApp(<AssistantPage />);
    const user = await ask('Hi');

    expect(await screen.findByText("Something went wrong on the assistant's side. Please try again.")).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Hello!')).toBeInTheDocument();
    expect(requests[1].messages.map((message) => message.content)).toEqual(['Hi']);
  });

  it('starts a new conversation with a new threadId', async () => {
    const requests = agentCore(run(...text('m1', 'Hello!')), run(...text('m2', 'Hi again.')));
    await renderWithApp(<AssistantPage />);
    const user = await ask('Hi');
    await screen.findByText('Hello!');

    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    await waitFor(() => expect(screen.queryByText('Hello!')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'What were my last 5 orders?' }));
    await screen.findByText('Hi again.');

    expect(requests[1].threadId).not.toBe(requests[0].threadId);
  });
});
