import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import type { Notification } from '@/shared/api/types';
import { listNotifications, markNotificationRead } from './api';

/** The backend suggests polling every 30-60 seconds (modules/notifications/src/notifications/api.py). */
const POLL_MS = 45_000;
const LIMIT = 50;

function newestFirst(items: Notification[]): Notification[] {
  return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** The latest notifications. `poll` keeps them fresh while the tab is visible (the header bell does). */
export function useNotifications({ poll = false }: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: qk.notifications(),
    queryFn: async ({ signal }) => newestFirst(await listNotifications(LIMIT, signal)),
    refetchInterval: poll ? POLL_MS : false,
  });
}

function markReadInCache(queryClient: ReturnType<typeof useQueryClient>, ids: string[]) {
  queryClient.setQueryData<Notification[]>(qk.notifications(), (items) =>
    items?.map((item) => (ids.includes(item.notification_id) ? { ...item, read: true } : item)),
  );
}

export function useMarkRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: markNotificationRead,
    onMutate: (id) => markReadInCache(queryClient, [id]),
    onError: () => queryClient.invalidateQueries({ queryKey: qk.notifications() }),
  });
}

/** There is no "mark all" endpoint: one call per unread notification (at most 50). */
export function useMarkAllRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => Promise.all(ids.map(markNotificationRead)),
    onMutate: (ids) => markReadInCache(queryClient, ids),
    onSettled: () => queryClient.invalidateQueries({ queryKey: qk.notifications() }),
  });
}
