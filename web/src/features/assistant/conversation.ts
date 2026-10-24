import type { Message, RunFinishedEvent } from '@ag-ui/client';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import { ApiError } from '@/shared/api/client';
import type { AssistantConversationDetail } from '@/shared/api/types';
import { endSession, getIdToken, refreshSession, sessionStore } from '@/shared/auth/session';
import { AssistantAgent, getConversation, MAIN_AGENT, type RunError, runError } from './api';
import { forgetAllSent, forgetSent, overdue, rememberSent, type Unanswered, unansweredIn } from './unanswered';

/**
 * The conversation on the Assistant page: the one streaming now, or one
 * reopened from its history (openConversation). It outlives the page, so the
 * user can look at an order and come back while an answer streams; signing
 * out or changing workspace clears it. The URL names the conversation, so a
 * reload reopens it from the server, waiting for an answer still coming.
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
/** Stops reading the history of a conversation whose answer the page waits for (waitForAnswer). */
let waiting: AbortController | null = null;

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

/**
 * Stops showing the answer, or waiting for it. The server still finishes it,
 * so the next message continues from all of it.
 */
export function stopAnswer(): void {
  agent?.abortRun();
  if (stopWaiting()) conversationStore.setState({ running: false });
}

export function newConversation(): void {
  agent?.abortRun();
  stopWaiting();
  agent = null;
  conversationStore.setState(fresh());
}

/**
 * Shows a conversation from its history (GET /assistant/conversations/{id}),
 * or, with none, from the message this tab sent in it: the server doesn't
 * have it yet. While its newest message is still being answered (the page
 * was reloaded or left meanwhile), it shows as running, and the history is
 * read again until the answer is in it. The next message continues it: same
 * threadId, so the agent picks up from its own checkpoints on the server.
 */
export function openConversation(threadId: string, history: AssistantConversationDetail | null): void {
  agent?.abortRun();
  stopWaiting();
  const sent = unansweredIn(threadId);
  const { messages, pending, lost } = awaited(history, sent);
  agent = new AssistantAgent(threadId, messages);
  conversationStore.setState({ threadId, messages, running: pending, error: lost ? NOT_RECEIVED : null, tokens: null });
  if (pending) void waitForAnswer(agent, sent);
  else if (!lost) forgetSent(threadId);
}

// Nothing from one user or workspace may show in the next, as main.tsx does
// for the query cache. Here rather than there: main.tsx would load the AG-UI
// client on every page, not only with this one.
sessionStore.subscribe((state, previous) => {
  if (state.status === 'signed-out' || state.user?.tenantId !== previous.user?.tenantId) {
    newConversation();
    forgetAllSent();
  }
});

/** A message sent before a reload that the server never got: sending it again (Try again) is all there is to do. */
const NOT_RECEIVED: RunError = { code: 'NETWORK_ERROR', message: 'The assistant never got the message' };
/** How often the history is read while an answer is awaited. */
const POLL_MS = 2000;

/**
 * What to show of a conversation, given its history (null: not on the server)
 * and the message this tab sent in it without seeing the answer: the message
 * stays on screen however far the server got. `pending`: an answer is still
 * coming. `lost`: the server never got the message.
 */
function awaited(history: AssistantConversationDetail | null, sent: Unanswered | null) {
  const messages = history?.messages ?? [];
  const missing = sent !== null && !messages.some((message) => message.id === sent.message.id);
  const shown: Message[] = missing ? [...messages, { id: sent.message.id, role: 'user', content: sent.message.content }] : messages;
  const late = missing && overdue(sent);
  return { messages: shown, pending: (history?.answering ?? false) || (missing && !late), lost: late && !history?.answering };
}

/** Reads the history until the awaited answer is in it, then shows the conversation with it. */
async function waitForAnswer(target: AssistantAgent, sent: Unanswered | null): Promise<void> {
  const controller = new AbortController();
  waiting = controller;
  const { signal } = controller;
  while (!signal.aborted) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    if (signal.aborted) return;
    let history: AssistantConversationDetail | null;
    try {
      history = await getConversation(target.threadId, signal);
    } catch (error) {
      if (signal.aborted) return;
      if (!(error instanceof ApiError && error.status === 404)) continue; // out of reach for now: read again
      history = null;
    }
    if (signal.aborted) return;
    const { messages, pending, lost } = awaited(history, sent);
    if (pending) continue;
    waiting = null;
    target.setMessages(messages);
    update(target, { messages, running: false, error: lost ? NOT_RECEIVED : null });
    if (!lost) forgetSent(target.threadId);
    return;
  }
}

/** True if the page was waiting for an answer. */
function stopWaiting(): boolean {
  const was = waiting;
  waiting = null;
  was?.abort();
  return was !== null;
}

async function answer(target: AssistantAgent): Promise<void> {
  remember(target);
  update(target, { running: true, error: null, tokens: null });
  let result = await run(target);
  // Rejected for the token before anything ran (nothing was counted): one retry
  // with new tokens, as the HTTP API client does. A failed refresh signs out.
  if (result.error && AUTH_CODES.has(result.error.code) && (await refreshSession())) {
    result = await run(target);
    if (result.error?.code === 'UNAUTHORIZED') await endSession('expired');
  }
  // The server said the run is over: nothing to wait for after a reload. Stopped
  // or cut off on the way, its answer may still be coming.
  if (result.ended) forgetSent(target.threadId);
  update(target, { running: false, error: result.error });
}

/** Until its answer ends here, the newest user message is remembered for a reload (unanswered.ts). */
function remember(target: AssistantAgent): void {
  const asked = target.messages.filter((message) => message.role === 'user');
  const newest = asked.at(-1);
  if (newest && typeof newest.content === 'string') {
    rememberSent(target.threadId, { id: newest.id, content: newest.content }, asked.length === 1);
  }
}

/** The code @ag-ui/client gives the RUN_ERROR it makes up when a run is aborted mid-stream. */
const ABORTED = 'abort';

interface RunResult {
  error: RunError | null;
  /** The server ended the run (RUN_FINISHED, RUN_ERROR, an HTTP error), or it never started. */
  ended: boolean;
}

async function run(target: AssistantAgent): Promise<RunResult> {
  const token = await getIdToken();
  if (!token) return { error: { code: 'UNAUTHORIZED', message: 'Not signed in' }, ended: true };
  target.authorize(token);
  let error: RunError | null = null;
  let ended = false;
  try {
    await target.runAgent(
      { forwardedProps: { agent: MAIN_AGENT } },
      {
        onMessagesChanged: ({ messages }) => update(target, { messages: [...messages] }),
        onRunErrorEvent: ({ event }) => {
          if (event.code === ABORTED) return; // the user stopped it, or left the conversation
          ended = true;
          error = { code: event.code ?? 'INTERNAL_ERROR', message: event.message };
        },
        onRunFinishedEvent: ({ event }) => {
          ended = true;
          update(target, { tokens: totalTokens(event) });
        },
      },
    );
  } catch (thrown) {
    error = runError(thrown);
    ended = error.code !== 'NETWORK_ERROR'; // an HTTP error is the server's answer; with none, it may have the message
  }
  return { error, ended };
}

function totalTokens(event: RunFinishedEvent): number | null {
  if (!event.usage?.length) return null;
  return event.usage.reduce((sum, usage) => sum + (usage.totalTokens ?? 0), 0);
}
