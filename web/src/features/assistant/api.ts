import { HttpAgent, type Message, type RunAgentInput } from '@ag-ui/client';
import { config } from '@/shared/config';

/**
 * The assistant's one endpoint: AgentCore Runtime's /invocations, which speaks
 * AG-UI (modules/assistant/README.md, "Calling it from the frontend"). The
 * official client, @ag-ui/client, sends the RunAgentInput, reads the events
 * and assembles them into messages.
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
 * the AgentCore session, so it also goes in the session header.
 */
export class AssistantAgent extends HttpAgent {
  constructor(threadId: string) {
    super({ url: config.assistantUrl, threadId });
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
