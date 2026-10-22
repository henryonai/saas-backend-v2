import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { Order, OrderCreate, OrderPage, OrderStatus } from '@/shared/api/types';

const PAGE_SIZE = 20;

/** Newest first, a page at a time (the API's cursor). */
export function useOrderList() {
  return useInfiniteQuery({
    queryKey: qk.orderList(),
    queryFn: ({ pageParam, signal }) =>
      api<OrderPage>('/orders', { query: { limit: PAGE_SIZE, cursor: pageParam }, signal }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useRecentOrders(limit = 5) {
  return useQuery({
    queryKey: qk.recentOrders(),
    queryFn: ({ signal }) => api<OrderPage>('/orders', { query: { limit }, signal }),
  });
}

export function useOrder(orderId: string) {
  return useQuery({
    queryKey: qk.order(orderId),
    queryFn: ({ signal }) => api<Order>(`/orders/${encodeURIComponent(orderId)}`, { signal }),
  });
}

/** Fails with 402 when the plan's order limit is reached. */
export function useCreateOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OrderCreate) => api<Order>('/orders', { method: 'POST', body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.orders() }),
  });
}

/** Fails with 400 for a change the order's status doesn't allow, 409 if someone changed it first. */
export function useChangeOrderStatus(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (status: OrderStatus) =>
      api<Order>(`/orders/${encodeURIComponent(orderId)}/status`, { method: 'PATCH', body: { status } }),
    onSuccess: (order) => {
      queryClient.setQueryData(qk.order(orderId), order);
      void queryClient.invalidateQueries({ queryKey: qk.orderList() });
      void queryClient.invalidateQueries({ queryKey: qk.recentOrders() });
    },
    onError: () => queryClient.invalidateQueries({ queryKey: qk.order(orderId) }),
  });
}
