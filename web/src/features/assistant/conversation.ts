import { type BaseEvent, EventType, type Message, type RunFinishedEvent } from '@ag-ui/client';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import type { AssistantConversationDetail, AssistantRating } from '@/shared/api/types';
import { endSession, getIdToken, refreshSession, sessionStore } from '@/shared/auth/session';
import {
  AssistantAgent,
  cancelRun,
  getConversation,
  getEarlierMessages,
  MAIN_AGENT,
  postMessage,
  rateMessage,
  type RunError,
  requestError,
  runError,
} from './api';

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
  /** the user's thumbs up or down on answers, by the id of each answer's last assistant message */
  ratings: Record<string, AssistantRating>;
  /**
   * A long conversation opens at its latest messages: where the ones before
   * them start (their earlier_cursor), or null when the page has them all.
   */
  earlier: string | null;
  loadingEarlier: boolean;
}

function fresh(): ConversationState {
  return {
    threadId: crypto.randomUUID(), messages: [], running: false, error: null, tokens: null, ratings: {}, earlier: null, loadingEarlier: false,
  };
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

/** Shows these messages, and has the agent client start from them. */
function show(target: AssistantAgent, messages: Message[]): void {
  target.setMessages(messages);
  update(target, { messages: [...messages] });
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
 * Edits one of the user's messages, as in ChatGPT: it and everything after
 * it make way for the new text, here and on the server, where the agent goes
 * back to before it too, and the new message is answered.
 */
export async function editMessage(messageId: string, text: string): Promise<void> {
  const prompt = text.trim();
  if (!prompt || conversationStore.getState().running) return;
  const target = currentAgent();
  const at = target.messages.findIndex((message) => message.id === messageId && message.role === 'user');
  if (at < 0) return;
  const id = crypto.randomUUID();
  show(target, [...target.messages.slice(0, at), { id, role: 'user', content: prompt }]);
  await ask(target, id, prompt, messageId);
}

/**
 * Asks for the last answer again: the newest user message goes out again
 * (counted again), and on the server it takes its own place, so the old
 * answer goes, from the history and from what the agent remembers.
 */
export async function regenerate(): Promise<void> {
  if (conversationStore.getState().running) return;
  const target = currentAgent();
  const at = lastUserMessage(target.messages);
  const newest = target.messages[at];
  if (!newest || typeof newest.content !== 'string') return;
  show(target, target.messages.slice(0, at + 1));
  await ask(target, newest.id, newest.content);
}

/**
 * Asks again after a failed run, as regenerate() does. A run the page
 * couldn't reach is followed again instead: it may well have answered.
 */
export async function retry(): Promise<void> {
  if (conversationStore.getState().running) return;
  const target = currentAgent();
  if (unreached?.agent === target) return follow(target, unreached.runId);
  await regenerate();
}

/**
 * Thumbs up or down on an answer (`messageId`: its last assistant message),
 * at once on screen; the same again takes it back. A failed save puts the
 * old rating back.
 */
export async function rateAnswer(messageId: string, rating: AssistantRating): Promise<void> {
  const { threadId, ratings } = conversationStore.getState();
  const before = ratings[messageId] ?? null;
  const after = before === rating ? null : rating;
  setRating(threadId, messageId, after);
  try {
    await rateMessage(threadId, messageId, after);
  } catch {
    setRating(threadId, messageId, before);
  }
}

function setRating(threadId: string, messageId: string, rating: AssistantRating | null): void {
  const state = conversationStore.getState();
  if (state.threadId !== threadId) return;
  const ratings = { ...state.ratings };
  if (rating) ratings[messageId] = rating;
  else delete ratings[messageId];
  conversationStore.setState({ ratings });
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
  conversationStore.setState({
    threadId, messages: history.messages, running: false, error: null, tokens: null, ratings: { ...history.ratings },
    earlier: history.earlier_cursor ?? null, loadingEarlier: false,
  });
  if (history.run_id) void follow(agent, history.run_id);
}

/**
 * Shows the messages before the oldest on screen, a page at a time (a long
 * conversation opens at its latest 200). Not while an answer streams: the
 * client is adding to the messages then. Throws what failed; the button stays.
 */
export async function loadEarlier(): Promise<void> {
  const { earlier, loadingEarlier, running } = conversationStore.getState();
  if (!earlier || loadingEarlier || running) return;
  const target = currentAgent();
  update(target, { loadingEarlier: true });
  try {
    const page = await getEarlierMessages(target.threadId, earlier);
    if (conversationStore.getState().running) return; // an answer started meanwhile: the button is there again after it
    show(target, [...page.messages, ...target.messages]);
    update(target, { earlier: page.earlier_cursor ?? null, ratings: { ...page.ratings, ...conversationStore.getState().ratings } });
  } finally {
    update(target, { loadingEarlier: false });
  }
}

// Nothing from one user or workspace may show in the next, as main.tsx does
// for the query cache. Here rather than there: main.tsx would load the AG-UI
// client on every page, not only with this one.
sessionStore.subscribe((state, previous) => {
  if (state.status === 'signed-out' || state.user?.tenantId !== previous.user?.tenantId) newConversation();
});

/** Saves the message with the run that will answer it (the HTTP API), then follows the run. */
async function ask(target: AssistantAgent, id: string, content: string, replaces?: string): Promise<void> {
  update(target, { running: true, error: null, tokens: null });
  let runId: string;
  try {
    ({ run_id: runId } = await postMessage(target.threadId, { id, content, agent: MAIN_AGENT, ...(replaces && { replaces }) }));
  } catch (error) {
    update(target, { running: false, error: requestError(error) });
    return;
  }
  await follow(target, runId);
}

/**
 * Streams a run into the state: one just sent, or one already going.
 * AgentCore starts it if nobody has, and sends every event from the first;
 * after a stream that broke, only those this page doesn't have.
 */
async function follow(target: AssistantAgent, runId: string): Promise<void> {
  const following = { agent: target, runId };
  shown = following;
  unreached = null;
  const before = [...target.messages]; // what the page showed before any of this run
  update(target, { running: true, error: null, tokens: null });
  let result = await stream(target, runId, before);
  // Rejected for the token before anything ran: one retry with new tokens, as
  // the HTTP API client does. A failed refresh signs out.
  if (result.error && AUTH_CODES.has(result.error.code) && (await refreshSession())) {
    result = await stream(target, runId, before);
    if (result.error?.code === 'UNAUTHORIZED') await endSession('expired');
  }
  let position = result.position;
  for (const delay of FOLLOW_DELAYS_MS) {
    if (!result.transient) break;
    await new Promise((resolve) => setTimeout(resolve, delay));
    if (shown !== following) {
      update(target, { running: false }); // stopped, or left, meanwhile
      return;
    }
    // Nowhere to read on from: AgentCore sends the run from the start, so take back what this page drew of it.
    if (!position) show(target, before);
    result = await stream(target, runId, before, position ?? undefined);
    position = result.position ?? position;
  }
  if (result.error?.code === 'NOT_FOUND') {
    // It ended before this page asked, and AgentCore has let it go: the history has the answer.
    await showHistory(target);
    return;
  }
  if (result.transient) unreached = following;
  update(target, { running: false, error: result.error });
}

/**
 * Where this page is in a run's events: how many it has (`after`), of the
 * telling that started at `startedAt` (its RUN_STARTED's timestamp). Sent
 * back as forwardedProps.resume, the server sends only the rest (app.resume_after).
 */
interface Position {
  after: number;
  startedAt: number;
}

interface Streamed {
  error: RunError | null;
  /** It failed on the way, not because of the run: AgentCore or the network. Asking again may get it. */
  transient: boolean;
  /** null: no RUN_STARTED with a timestamp came, so there is nothing to read on from */
  position: Position | null;
}

async function stream(target: AssistantAgent, runId: string, before: Message[], resume?: Position): Promise<Streamed> {
  const token = await getIdToken();
  if (!token) return { error: { code: 'UNAUTHORIZED', message: 'Not signed in' }, transient: false, position: null };
  target.authorize(token);
  let error: RunError | null = null;
  let transient = false;
  const at: { position: Position | null } = { position: null };
  const drawn = idsOf(target.messages);
  try {
    await target.runAgent(
      { runId, forwardedProps: { agent: MAIN_AGENT, ...(resume && { resume }) } },
      {
        onRunStartedEvent: ({ event }) => {
          if (resume && event.timestamp === resume.startedAt) {
            at.position = { ...resume }; // the server reads on from there
            return;
          }
          at.position = typeof event.timestamp === 'number' ? { after: 1, startedAt: event.timestamp } : null;
          // Asked to read on, it sent another telling (the run started again elsewhere): from the start.
          return resume ? { messages: before } : undefined;
        },
        onEvent: ({ event }) => {
          if (at.position && isLogged(event, drawn)) at.position.after += 1;
        },
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
  return { error, transient, position: at.position };
}

/**
 * Whether the event is one of the run's own, as the server counts them:
 * not the stream's opening and end, nor the start of a message or tool
 * call this page had already, which a stream read on from the middle of it
 * sends again (runs.reopened).
 */
function isLogged(event: BaseEvent, drawn: Set<string>): boolean {
  switch (event.type) {
    case EventType.RUN_STARTED:
    case EventType.RUN_FINISHED:
    case EventType.RUN_ERROR:
      return false;
    case EventType.TEXT_MESSAGE_START:
      return !drawn.has((event as BaseEvent & { messageId: string }).messageId);
    case EventType.TOOL_CALL_START:
      return !drawn.has((event as BaseEvent & { toolCallId: string }).toolCallId);
    default:
      return true;
  }
}

/** The ids of these messages and of their tool calls. */
function idsOf(messages: Message[]): Set<string> {
  const ids = new Set<string>();
  for (const message of messages) {
    ids.add(message.id);
    if (message.role === 'assistant') for (const call of message.toolCalls ?? []) ids.add(call.id);
  }
  return ids;
}

function lastUserMessage(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'user') return i;
  return -1;
}

async function showHistory(target: AssistantAgent): Promise<void> {
  try {
    const { messages, ratings, earlier_cursor } = await getConversation(target.threadId);
    target.setMessages(messages);
    update(target, { messages, ratings: { ...ratings }, earlier: earlier_cursor ?? null, running: false, error: null });
  } catch (error) {
    update(target, { running: false, error: requestError(error) });
  }
}

function totalTokens(event: RunFinishedEvent): number | null {
  if (!event.usage?.length) return null;
  return event.usage.reduce((sum, usage) => sum + (usage.totalTokens ?? 0), 0);
}
