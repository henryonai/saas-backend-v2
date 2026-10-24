import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

/**
 * Messages this tab sent and hasn't seen the answer to, by conversation, kept
 * in sessionStorage. The server finishes an answer whatever the page does,
 * but a page reloaded (or left) meanwhile only knows what the server has:
 * maybe not even the message, since a new conversation is a new AgentCore
 * session, which takes seconds to start. With this, it shows the message
 * waiting for its answer (conversation.ts) and the conversation in the list.
 */
const KEY = 'saas.assistantUnanswered';
/** A message the server still doesn't have this long after it was sent never reached it. */
const ARRIVAL_MS = 60_000;

export interface Unanswered {
  message: { id: string; content: string };
  /** the conversation's first message: the server may not list the conversation yet */
  first: boolean;
  sentAt: number;
}

type ByThread = Record<string, Unanswered>;

function load(): ByThread {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as ByThread;
  } catch {
    return {};
  }
}

const unansweredStore = createStore<ByThread>()(load);

unansweredStore.subscribe((state) => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // storage disabled: a reloaded page just won't wait for the answer
  }
});

export function rememberSent(threadId: string, message: Unanswered['message'], first: boolean): void {
  unansweredStore.setState({ [threadId]: { message, first, sentAt: Date.now() } });
}

export function forgetSent(threadId: string): void {
  const { [threadId]: forgotten, ...rest } = unansweredStore.getState();
  if (forgotten) unansweredStore.setState(rest, true);
}

/** Signing out or changing workspace: nothing of one user's may show for the next. */
export function forgetAllSent(): void {
  unansweredStore.setState({}, true);
}

/** The message this tab sent in the conversation and hasn't seen answered, however long ago. */
export function unansweredIn(threadId: string): Unanswered | null {
  return unansweredStore.getState()[threadId] ?? null;
}

/** Sent too long ago for a server that doesn't have it to still get it. */
export function overdue(sent: Unanswered, now = Date.now()): boolean {
  return now - sent.sentAt >= ARRIVAL_MS;
}

/** Conversations this tab started whose first message may not be on the server yet, newest first. */
export function useStartedConversations(): [string, Unanswered][] {
  const sent = useStore(unansweredStore);
  return Object.entries(sent)
    .filter(([, unanswered]) => unanswered.first && !overdue(unanswered))
    .sort(([, a], [, b]) => b.sentAt - a.sentAt);
}
