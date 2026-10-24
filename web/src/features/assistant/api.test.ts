import { describe, expect, it, vi } from 'vitest';
import { AssistantAgent, runError } from './api';

vi.mock('@/shared/config', () => ({ config: { assistantUrl: 'https://agent.test/invocations?qualifier=DEFAULT' } }));

const THREAD = '0f6c1d1e-4b0a-4f5e-9a51-6f3a3e2b7c11';

describe('AssistantAgent', () => {
  it('sends the token, the threadId as the session, and the run: no messages (the server saved them)', async () => {
    const fetch = vi.fn(async (_url: string, init: RequestInit) => {
      const { threadId, runId } = JSON.parse(String(init.body));
      return new Response(
        [{ type: 'RUN_STARTED', threadId, runId }, { type: 'RUN_FINISHED', threadId, runId }].map((e) => `data: ${JSON.stringify(e)}\n\n`).join(''),
        { headers: { 'Content-Type': 'text/event-stream' } },
      );
    });
    vi.stubGlobal('fetch', fetch);
    const agent = new AssistantAgent(THREAD);
    agent.addMessage({ id: 'u1', role: 'user', content: 'First' });
    agent.addMessage({ id: 'a1', role: 'assistant', content: 'An answer' });
    agent.addMessage({ id: 'u2', role: 'user', content: 'Second' });
    agent.authorize('id-token');

    await agent.runAgent({ runId: 'run-1', forwardedProps: { agent: 'assistant' } });

    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('https://agent.test/invocations?qualifier=DEFAULT');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer id-token', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': THREAD });
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({ threadId: THREAD, runId: 'run-1', messages: [], forwardedProps: { agent: 'assistant' } });
    expect(agent.messages).toHaveLength(3); // what the page shows stays whole
  });
});

describe('runError', () => {
  it("reads AgentCore's RUN_ERROR from an error response's body", () => {
    const thrown = Object.assign(new Error('HTTP 403'), {
      status: 403,
      payload: 'data: {"type":"RUN_ERROR","message":"OAuth authorization failed: Token expired","code":"ACCESS_DENIED"}\n\n',
    });
    expect(runError(thrown)).toEqual({ code: 'ACCESS_DENIED', message: 'OAuth authorization failed: Token expired' });
  });

  it('falls back on the HTTP status, and calls anything without one a network error', () => {
    expect(runError(Object.assign(new Error('HTTP 502'), { status: 502, payload: '<html>Bad gateway</html>' }))).toEqual({
      code: 'INTERNAL_ERROR',
      message: 'HTTP 502',
    });
    expect(runError(Object.assign(new Error('HTTP 429'), { status: 429, payload: '' })).code).toBe('RATE_LIMIT_EXCEEDED');
    expect(runError(new TypeError('Failed to fetch')).code).toBe('NETWORK_ERROR');
  });
});
