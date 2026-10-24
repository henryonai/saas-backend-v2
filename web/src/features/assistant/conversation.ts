import type { Message, RunFinishedEvent } from '@ag-ui/client';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import type { AssistantConversationDetail } from '@/shared/api/types';
import { endSession, getIdToken, refreshSession, sessionStore } from '@/shared/auth/session';
import { AssistantAgent, cancelRun, getConversation, MAIN_AGENT, postMessage, type RunError, requestError, runError } from './api';

/**
 * The conversation on the Assistant page: the one streaming now, or one
 * reopened from its history (openConversation). It outlives the page, so the
 * user can look at an order and come back while an answer streams; signing
 * out or changing workspace clears it.
 *
 * As in ChatGPT, the server keeps it all. Sending saves the message, with the
 * run that will answer it, before anything runs (the HTTP API); the run goes
 * on whatever this page does, and AgentCore streams it to whoever asks for
 * it, from the start. So the URL names the conversation, and a reload reopens
 * it from the server and, while its answer is coming, follows the run again.
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
/** The run on screen, which Stop stops. */
let shown: { agent: AssistantAgent; runId: string } | null = null;
/** A run the page gave up following (AgentCore unreachable): Try again follows it, rather than asking again. */
let unreached: { agent: AssistantAgent; runId: string } | null = null;

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
/** The code @ag-ui/client gives the RUN_ERROR it makes up when a run is aborted mid-stream. */
const ABORTED = 'abort';
/**
 * AgentCore's own RUN_ERROR (in a 200 stream) when it got no answer from the
 * session's microVM: "Received error (502) from runtime". Not the run's.
 */
const AGENTCORE_ERROR = 'AGENT_ERROR';
/**
 * Waits before following a run again when its stream failed on the way (a
 * dropped connection, AgentCore busy or failing): the run goes on, so it is
 * worth asking again. A new session answers a second request with
 * AGENT_ERROR until its microVM has started, a few seconds: what a reload
 * right after sending meets (dev, October 2026).
 */
const FOLLOW_DELAYS_MS = [1000, 2000, 4000, 8000];

/** Sends the user's message and streams the answer into the state. */
export async function sendMessage(text: string): Promise<void> {
  const prompt = text.trim();
  if (!prompt || conversationStore.getState().running) return;
  const target = currentAgent();
  const id = crypto.randomUUID();
  target.addMessage({ id, role: 'user', content: prompt });
  conversationStore.setState({ messages: [...target.messages] });
  await ask(target, id, prompt);
}

/**
 * Asks again after a failed run: the newest user message goes out again
 * (counted again, not saved twice). A run the page couldn't reach is
 * followed again instead: it may well have answered.
 */
export async function retry(): Promise<void> {
  if (conversationStore.getState().running) return;
  const target = currentAgent();
  if (unreached?.agent === target) return follow(target, unreached.runId);
  const newest = [...target.messages].reverse().find((message) => message.role === 'user');
  if (newest && typeof newest.content === 'string') await ask(target, newest.id, newest.content);
}

/** Stops the answer: on screen at once, and on the server, which keeps what it wrote. */
export function stopAnswer(): void {
  if (shown && shown.agent === agent) void cancelRun(shown.agent.threadId, shown.runId).catch(() => {});
  shown = null;
  agent?.abortRun();
}

/** Leaves the conversation on screen: its answer, if one is coming, goes on and is there to reopen. */
export function newConversation(): void {
  agent?.abortRun();
  agent = shown = unreached = null;
  conversationStore.setState(fresh());
}

/**
 * Shows a conversation from its history (GET /assistant/conversations/{id}).
 * While its newest message is still being answered (`run_id`: the page was
 * reloaded or left meanwhile), it follows that run: AgentCore sends what it
 * said so far, then the rest. The next message continues the conversation:
 * same threadId, so the agent picks up from its own checkpoints on the server.
 */
export function openConversation(threadId: string, history: AssistantConversationDetail): void {
  agent?.abortRun();
  shown = unreached = null;
  agent = new AssistantAgent(threadId, history.messages);
  conversationStore.setState({ threadId, messages: history.messages, running: false, error: null, tokens: null });
  if (history.run_id) void follow(agent, history.run_id);
}

// Nothing from one user or workspace may show in the next, as main.tsx does
// for the query cache. Here rather than there: main.tsx would load the AG-UI
// client on every page, not only with this one.
sessionStore.subscribe((state, previous) => {
  if (state.status === 'signed-out' || state.user?.tenantId !== previous.user?.tenantId) newConversation();
});

/** Saves the message with the run that will answer it (the HTTP API), then follows the run. */
async function ask(target: AssistantAgent, id: string, content: string): Promise<void> {
  update(target, { running: true, error: null, tokens: null });
  let runId: string;
  try {
    ({ run_id: runId } = await postMessage(target.threadId, { id, content, agent: MAIN_AGENT }));
  } catch (error) {
    update(target, { running: false, error: requestError(error) });
    return;
  }
  await follow(target, runId);
}

/**
 * Streams a run into the state: one just sent, or one already going.
 * AgentCore starts it if nobody has, and sends every event from the first.
 */
async function follow(target: AssistantAgent, runId: string): Promise<void> {
  const following = { agent: target, runId };
  shown = following;
  unreached = null;
  const before = [...target.messages]; // what the page showed before any of this run
  update(target, { running: true, error: null, tokens: null });
  let result = await stream(target, runId);
  // Rejected for the token before anything ran: one retry with new tokens, as
  // the HTTP API client does. A failed refresh signs out.
  if (result.error && AUTH_CODES.has(result.error.code) && (await refreshSession())) {
    result = await stream(target, runId);
    if (result.error?.code === 'UNAUTHORIZED') await endSession('expired');
  }
  for (const delay of FOLLOW_DELAYS_MS) {
    if (!result.transient) break;
    await new Promise((resolve) => setTimeout(resolve, delay));
    if (shown !== following) {
      update(target, { running: false }); // stopped, or left, meanwhile
      return;
    }
    // AgentCore sends the run from the start again: take back what this page drew of it.
    target.setMessages(before);
    update(target, { messages: before });
    result = await stream(target, runId);
  }
  if (result.error?.code === 'NOT_FOUND') {
    // It ended before this page asked, and AgentCore has let it go: the history has the answer.
    await showHistory(target);
    return;
  }
  if (result.transient) unreached = following;
  update(target, { running: false, error: result.error });
}

interface Streamed {
  error: RunError | null;
  /** It failed on the way, not because of the run: AgentCore or the network. Asking again may get it. */
  transient: boolean;
}

async function stream(target: AssistantAgent, runId: string): Promise<Streamed> {
  const token = await getIdToken();
  if (!token) return { error: { code: 'UNAUTHORIZED', message: 'Not signed in' }, transient: false };
  target.authorize(token);
  let error: RunError | null = null;
  let transient = false;
  try {
    await target.runAgent(
      { runId, forwardedProps: { agent: MAIN_AGENT } },
      {
        onMessagesChanged: ({ messages }) => update(target, { messages: [...messages] }),
        onRunErrorEvent: ({ event }) => {
          if (event.code === ABORTED) return; // the user stopped it, or left the conversation
          error = { code: event.code ?? 'INTERNAL_ERROR', message: event.message };
          transient = event.code === AGENTCORE_ERROR;
        },
        onRunFinishedEvent: ({ event }) => update(target, { tokens: totalTokens(event) }),
      },
    );
  } catch (thrown) {
    error = runError(thrown);
    const { status } = (thrown ?? {}) as { status?: unknown };
    transient = typeof status !== 'number' || status >= 500 || status === 429;
  }
  return { error, transient };
}

async function showHistory(target: AssistantAgent): Promise<void> {
  try {
    const { messages } = await getConversation(target.threadId);
    target.setMessages(messages);
    update(target, { messages, running: false, error: null });
  } catch (error) {
    update(target, { running: false, error: requestError(error) });
  }
}

function totalTokens(event: RunFinishedEvent): number | null {
  if (!event.usage?.length) return null;
  return event.usage.reduce((sum, usage) => sum + (usage.totalTokens ?? 0), 0);
}
