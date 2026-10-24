import { HttpAgent, type Message, type RunAgentInput } from '@ag-ui/client';
import { api } from '@/shared/api/client';
import type { AssistantConversation, AssistantConversationDetail, AssistantConversationPage } from '@/shared/api/types';
import { config } from '@/shared/config';

/**
 * The assistant has two backends (modules/assistant/README.md):
 * - the chat: AgentCore Runtime's /invocations, which speaks AG-UI. The
 *   official client, @ag-ui/client, sends the RunAgentInput, reads the events
 *   and assembles them into messages.
 * - the user's conversations: /assistant/conversations on the HTTP API, which
 *   returns a conversation's history as the same AG-UI messages.
 */

/** The agent the frontend talks to (modules/assistant/src/assistant/agents/assistant.md). */
export const MAIN_AGENT = 'assistant';

/** A failed run: the RUN_ERROR event's `code` and `message`, or NETWORK_ERROR when nothing answered. */
export interface RunError {
  code: string;
  message: string;
}

/**
 * The AG-UI client for one conversation. The threadId is the conversation and
 * the AgentCore session, so it also goes in the session header. `messages`:
 * the history of a conversation that is reopened.
 */
export class AssistantAgent extends HttpAgent {
  constructor(threadId: string, messages: Message[] = []) {
    super({ url: config.assistantUrl, threadId, initialMessages: messages });
  }

  /** Before each run: a current ID token (AgentCore's authorizer checks it, as API Gateway does). */
  authorize(idToken: string): void {
    this.headers = {
      Authorization: `Bearer ${idToken}`,
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': this.threadId,
    };
  }

  /**
   * Only the newest user message goes out: the server keeps the conversation
   * and reads nothing else, so the request stays small however long it gets.
   */
  protected override requestInit(input: RunAgentInput): RequestInit {
    const newest = lastUserMessage(input.messages);
    return { ...super.requestInit(input), body: JSON.stringify({ ...input, messages: newest ? [newest] : [] }) };
  }
}

function lastUserMessage(messages: Message[]): Message | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') return messages[i];
  }
  return undefined;
}

/** By HTTP status, for an error body that isn't a RUN_ERROR event (the names schemas.ERROR_CODES uses). */
const STATUS_CODES: Record<number, string> = {
  401: 'UNAUTHORIZED',
  403: 'ACCESS_DENIED',
  429: 'RATE_LIMIT_EXCEEDED',
  503: 'SERVICE_BUSY',
};

/**
 * What a failed `runAgent` threw. Errors from our code arrive as RUN_ERROR
 * events in a 200 stream; AgentCore's own (a missing or bad token, throttling)
 * come with an HTTP error status, and the client throws them with the body,
 * which is a RUN_ERROR event too.
 */
export function runError(error: unknown): RunError {
  const { status, payload } = (error ?? {}) as { status?: unknown; payload?: unknown };
  if (typeof status !== 'number') return { code: 'NETWORK_ERROR', message: String(error) };
  const event = typeof payload === 'string' ? firstEvent(payload) : null;
  if (event && typeof event.message === 'string') {
    return { code: typeof event.code === 'string' ? event.code : codeOf(status), message: event.message };
  }
  return { code: codeOf(status), message: `HTTP ${status}` };
}

function codeOf(status: number): string {
  return STATUS_CODES[status] ?? (status >= 500 ? 'INTERNAL_ERROR' : 'VALIDATION_ERROR');
}

/** The user's conversations, most recently used first. Pass `next_cursor` as `cursor` for the next page. */
export function listConversations(query: { limit: number; cursor?: string }, signal?: AbortSignal) {
  return api<AssistantConversationPage>('/assistant/conversations', { query, signal });
}

/** A conversation and its messages (the latest 200 of a long one). 404 once deleted or expired. */
export function getConversation(threadId: string, signal?: AbortSignal) {
  return api<AssistantConversationDetail>(`/assistant/conversations/${encodeURIComponent(threadId)}`, { signal });
}

export function renameConversation(threadId: string, title: string) {
  return api<AssistantConversation>(`/assistant/conversations/${encodeURIComponent(threadId)}`, { method: 'PATCH', body: { title } });
}

/** Gone at once; the server deletes the agent's own copy in the background. */
export function deleteConversation(threadId: string) {
  return api<void>(`/assistant/conversations/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
}

/** The first `data:` line of an SSE body, parsed. */
function firstEvent(body: string): Record<string, unknown> | null {
  const line = body.split('\n').find((text) => text.startsWith('data:'));
  if (!line) return null;
  try {
    const event: unknown = JSON.parse(line.slice('data:'.length));
    return event && typeof event === 'object' ? (event as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
