import { api } from '@/shared/api/client';
import type { Order, OrderCreate, OrderPage, OrderStatus } from '@/shared/api/types';

/** Newest first. Pass `next_cursor` from the previous page as `cursor` to continue. */
export function listOrders(query: { limit: number; cursor?: string }, signal?: AbortSignal) {
  return api<OrderPage>('/orders', { query, signal });
}

export function getOrder(orderId: string, signal?: AbortSignal) {
  return api<Order>(`/orders/${encodeURIComponent(orderId)}`, { signal });
}

/**
 * Fails with 402 when the plan's order limit is reached. Send the same
 * `idempotencyKey` again to retry: the API returns the order the first
 * request made instead of making another.
 */
export function createOrder(body: OrderCreate, idempotencyKey: string) {
  return api<Order>('/orders', { method: 'POST', body, headers: { 'Idempotency-Key': idempotencyKey } });
}

/** Fails with 400 for a change the order's status doesn't allow, 409 if someone changed it first. */
export function changeOrderStatus(orderId: string, status: OrderStatus) {
  return api<Order>(`/orders/${encodeURIComponent(orderId)}/status`, { method: 'PATCH', body: { status } });
}
