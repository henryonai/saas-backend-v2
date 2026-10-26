import { HttpAgent, type Message, type RunAgentInput } from '@ag-ui/client';
import { ApiError, api } from '@/shared/api/client';
import type {
  AssistantConversation,
  AssistantConversationDetail,
  AssistantConversationPage,
  AssistantRating,
  AssistantRun,
} from '@/shared/api/types';
import { config } from '@/shared/config';

/**
 * The assistant has two backends (modules/assistant/README.md):
 * - the user's conversations: /assistant/conversations on the HTTP API. A
 *   message is sent here first: it is saved, with the run that will answer
 *   it. A conversation's history comes back as AG-UI messages.
 * - the answers: AgentCore Runtime's /invocations, which speaks AG-UI. The
 *   official client, @ag-ui/client, sends it the run and assembles the events
 *   into messages; sent again, it follows the same run from the start.
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
 * the history of a conversation that is reopened. Each run is one the HTTP
 * API created (`runAgent({ runId })`).
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
   * No messages go out: the server answers the message it saved for the run,
   * and reads nothing else, so the request stays small however long the
   * conversation gets.
   */
  protected override requestInit(input: RunAgentInput): RequestInit {
    return { ...super.requestInit(input), body: JSON.stringify({ ...input, messages: [] }) };
  }
}

/** By HTTP status, for an error that isn't a RUN_ERROR event (the names schemas.ERROR_CODES uses). */
const STATUS_CODES: Record<number, string> = {
  401: 'UNAUTHORIZED',
  402: 'PLAN_LIMIT_REACHED',
  403: 'ACCESS_DENIED',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
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

/**
 * The user sent the plan's messages for this hour: the HTTP API's 429, whose
 * message says when to try again. (AgentCore's own throttling is
 * RATE_LIMIT_EXCEEDED, and worth a retry.)
 */
export const HOURLY_LIMIT = 'HOURLY_LIMIT_REACHED';

/** A failed HTTP API call as a run's error, so the page shows it the same way (402: the upgrade notice). */
export function requestError(error: unknown): RunError {
  if (error instanceof ApiError) return { code: error.status === 429 ? HOURLY_LIMIT : codeOf(error.status), message: error.message };
  return { code: 'NETWORK_ERROR', message: String(error) };
}

/**
 * Sends the user's message: the server saves it (creating the conversation
 * the first time) with the run that will answer it, and counts it against
 * the plan. `replaces`: an earlier message of the user's that this one takes
 * the place of, with everything after it (an edit); the same id sent again
 * replaces itself (Try again). 402 at the plan's limit; 429 at the user's
 * hourly limit; 409 while the previous answer is coming.
 */
export function postMessage(threadId: string, message: { id: string; content: string; agent: string; replaces?: string }) {
  return api<AssistantRun>(`/assistant/conversations/${encodeURIComponent(threadId)}/messages`, { method: 'POST', body: message });
}

/** Thumbs up or down on an answer (`messageId`: its last assistant message); null takes the rating back. */
export function rateMessage(threadId: string, messageId: string, rating: AssistantRating | null) {
  return api<void>(`/assistant/conversations/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}`, {
    method: 'PATCH',
    body: { rating },
  });
}

/** Stops the answer on the server, which keeps what it wrote. Fine after the end too. */
export function cancelRun(threadId: string, runId: string) {
  return api<void>(`/assistant/conversations/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
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
