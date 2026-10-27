/**
 * TanStack Query keys. The whole cache is cleared when the tab switches to
 * another workspace (main.tsx), so keys don't carry the tenant id.
 */
export const qk = {
  tenant: () => ['tenant'] as const,
  workspaces: () => ['workspaces'] as const,
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
  memories: () => ['assistant', 'memories'] as const,
  files: () => ['assistant', 'files'] as const,
  fileList: (filters: Record<string, string | undefined>) => ['assistant', 'files', 'list', filters] as const,
  file: (fileId: string, version?: number) => ['assistant', 'files', 'detail', fileId, version ?? null] as const,
};
