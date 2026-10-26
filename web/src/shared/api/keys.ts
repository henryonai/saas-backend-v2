/**
 * TanStack Query keys. The whole cache is cleared when the user's workspace
 * changes (main.tsx), so keys don't carry the tenant id.
 */
export const qk = {
  tenant: () => ['tenant'] as const,
  orders: () => ['orders'] as const,
  orderList: () => ['orders', 'list'] as const,
  recentOrders: () => ['orders', 'recent'] as const,
  order: (orderId: string) => ['orders', 'detail', orderId] as const,
  members: () => ['members'] as const,
  sentInvitations: () => ['invitations', 'sent'] as const,
  receivedInvitations: () => ['invitations', 'received'] as const,
  billing: () => ['billing'] as const,
  notifications: () => ['notifications'] as const,
  conversations: () => ['assistant', 'conversations'] as const,
  conversation: (threadId: string) => ['assistant', 'conversation', threadId] as const,
};
