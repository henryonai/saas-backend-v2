import { type InfiniteData, type QueryClient, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import type { AssistantConversation, AssistantConversationPage } from '@/shared/api/types';
import { deleteConversation, getConversation, listConversations, renameConversation } from './api';

const PAGE_SIZE = 30;
/** The server's limit on a title (service.TITLE_CHARS); a first message is cut to it. */
export const TITLE_CHARS = 80;

type ConversationPages = InfiniteData<AssistantConversationPage, string | undefined>;

/** The user's conversations, most recently used first, a page at a time. */
export function useConversations() {
  return useInfiniteQuery({
    queryKey: qk.conversations(),
    queryFn: ({ pageParam, signal }) => listConversations({ limit: PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

/**
 * A conversation's history, to reopen it. Never kept once its page closes:
 * the conversation grows with every message, so a kept copy would reopen it
 * without the newest ones.
 */
export function useConversationHistory(threadId: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.conversation(threadId),
    queryFn: ({ signal }) => getConversation(threadId, signal),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}

export function useRenameConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, title }: { threadId: string; title: string }) => renameConversation(threadId, title),
    onSuccess: (conversation) => updateList(queryClient, (items) =>
      items.map((item) => (item.thread_id === conversation.thread_id ? conversation : item)),
    ),
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: (_, threadId) => {
      updateList(queryClient, (items) => items.filter((item) => item.thread_id !== threadId));
      queryClient.removeQueries({ queryKey: qk.conversation(threadId) });
    },
  });
}

/**
 * Puts a conversation that just started at the top of the list, titled with
 * its first message as the server does, until the list is read again with the
 * model's title (after the answer).
 */
export function showNewConversation(queryClient: QueryClient, threadId: string, firstMessage: string): void {
  const now = new Date().toISOString();
  const conversation: AssistantConversation = {
    thread_id: threadId, title: firstMessageTitle(firstMessage), created_at: now, updated_at: now, message_count: 1,
  };
  updateList(queryClient, (items, first) => {
    const others = items.filter((item) => item.thread_id !== threadId);
    return first ? [conversation, ...others] : others;
  });
}

/** After an answer: the order changed, and a new conversation has its model-written title now. */
export function refreshConversations(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: qk.conversations() });
}

/** The server's first-message title (service.first_message_title): one line, cut to 80 characters. */
export function firstMessageTitle(text: string): string {
  const line = text.split(/\s+/).filter(Boolean).join(' ');
  return line.length <= TITLE_CHARS ? line : `${line.slice(0, TITLE_CHARS - 1).trimEnd()}…`;
}

/** Edits each loaded page of the list (`first`: the newest page). Nothing to do before the list loads. */
function updateList(
  queryClient: QueryClient,
  change: (items: AssistantConversation[], first: boolean) => AssistantConversation[],
): void {
  queryClient.setQueryData<ConversationPages>(qk.conversations(), (data) =>
    data && { ...data, pages: data.pages.map((page, index) => ({ ...page, items: change(page.items, index === 0) })) },
  );
}
