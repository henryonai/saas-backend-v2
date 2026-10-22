import { api } from '@/shared/api/client';
import type { Notification } from '@/shared/api/types';

/** The signed-in user's latest notifications (at most 50). */
export function listNotifications(limit: number, signal?: AbortSignal) {
  return api<Notification[]>('/notifications', { query: { limit }, signal });
}

export function markNotificationRead(notificationId: string) {
  return api<void>(`/notifications/${encodeURIComponent(notificationId)}/read`, { method: 'POST' });
}
