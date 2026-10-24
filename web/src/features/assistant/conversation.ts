import type { Message, RunFinishedEvent } from '@ag-ui/client';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import { endSession, getIdToken, refreshSession, sessionStore } from '@/shared/auth/session';
import { AssistantAgent, MAIN_AGENT, type RunError, runError } from './api';

/**
 * The conversation on the Assistant page. It outlives the page, so the user
 * can look at an order and come back; a reload, "New conversation", signing
 * out or changing workspace starts a new one. The server keeps every
 * conversation, but has no way yet to list or reopen one.
 */
export interface ConversationState {
  threadId: string;
  /** AG-UI messages as @ag-ui/client assembles them: user, assistant (text, tool calls), tool (results) */
  messages: Message[];
  running: boolean;
  error: RunError | null;
  /** tokens of the last answer, from RUN_FINISHED (a subagent's count against the plan but aren't in it) */
  tokens: number | null;
}

function fresh(): ConversationState {
  return { threadId: crypto.randomUUID(), messages: [], running: false, error: null, tokens: null };
}

export const conversationStore = createStore<ConversationState>()(fresh);

export function useConversation(): ConversationState {
  return useStore(conversationStore);
}

let agent: AssistantAgent | null = null;

function currentAgent(): AssistantAgent {
  const { threadId } = conversationStore.getState();
  if (agent?.threadId !== threadId) agent = new AssistantAgent(threadId);
  return agent;
}

/** Updates the state, unless the conversation `from` belongs to was replaced meanwhile. */
function update(from: AssistantAgent, changes: Partial<ConversationState>): void {
  if (conversationStore.getState().threadId === from.threadId) conversationStore.setState(changes);
}

/** The run failed because of the token: AgentCore's authorizer, or our own check of its claims. */
const AUTH_CODES = new Set(['UNAUTHORIZED', 'ACCESS_DENIED']);

/** Sends the user's message and streams the answer into the state. */
export async function sendMessage(text: string): Promise<void> {
  const prompt = text.trim();
  if (!prompt || conversationStore.getState().running) return;
  const target = currentAgent();
  target.addMessage({ id: crypto.randomUUID(), role: 'user', content: prompt });
  conversationStore.setState({ messages: [...target.messages] });
  await answer(target);
}

/** Asks again after a failed run: the newest user message goes out again (the server counts it again). */
export async function retry(): Promise<void> {
  if (conversationStore.getState().running) return;
  await answer(currentAgent());
}

/** Stops showing the answer. The server still finishes it, so the next message continues from all of it. */
export function stopAnswer(): void {
  agent?.abortRun();
}

export function newConversation(): void {
  agent?.abortRun();
  agent = null;
  conversationStore.setState(fresh());
}

// Nothing from one user or workspace may show in the next, as main.tsx does
// for the query cache. Here rather than there: main.tsx would load the AG-UI
// client on every page, not only with this one.
sessionStore.subscribe((state, previous) => {
  if (state.status === 'signed-out' || state.user?.tenantId !== previous.user?.tenantId) newConversation();
});

async function answer(target: AssistantAgent): Promise<void> {
  update(target, { running: true, error: null, tokens: null });
  let error = await run(target);
  // Rejected for the token before anything ran (nothing was counted): one retry
  // with new tokens, as the HTTP API client does. A failed refresh signs out.
  if (error && AUTH_CODES.has(error.code) && (await refreshSession())) {
    error = await run(target);
    if (error?.code === 'UNAUTHORIZED') await endSession('expired');
  }
  update(target, { running: false, error });
}

async function run(target: AssistantAgent): Promise<RunError | null> {
  const token = await getIdToken();
  if (!token) return { code: 'UNAUTHORIZED', message: 'Not signed in' };
  target.authorize(token);
  let error: RunError | null = null;
  try {
    await target.runAgent(
      { forwardedProps: { agent: MAIN_AGENT } },
      {
        onMessagesChanged: ({ messages }) => update(target, { messages: [...messages] }),
        onRunErrorEvent: ({ event }) => {
          error = { code: event.code ?? 'INTERNAL_ERROR', message: event.message };
        },
        onRunFinishedEvent: ({ event }) => update(target, { tokens: totalTokens(event) }),
      },
    );
  } catch (thrown) {
    error = runError(thrown);
  }
  return error;
}

function totalTokens(event: RunFinishedEvent): number | null {
  if (!event.usage?.length) return null;
  return event.usage.reduce((sum, usage) => sum + (usage.totalTokens ?? 0), 0);
}
