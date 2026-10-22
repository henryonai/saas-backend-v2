import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import type { OrderCreate, OrderStatus } from '@/shared/api/types';
import { changeOrderStatus, createOrder, getOrder, listOrders } from './api';

const PAGE_SIZE = 20;

/** A page at a time (the API's cursor). */
export function useOrderList() {
  return useInfiniteQuery({
    queryKey: qk.orderList(),
    queryFn: ({ pageParam, signal }) => listOrders({ limit: PAGE_SIZE, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useRecentOrders(limit = 5) {
  return useQuery({
    queryKey: qk.recentOrders(),
    queryFn: ({ signal }) => listOrders({ limit }, signal),
  });
}

export function useOrder(orderId: string) {
  return useQuery({
    queryKey: qk.order(orderId),
    queryFn: ({ signal }) => getOrder(orderId, signal),
  });
}

export function useCreateOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OrderCreate) => createOrder(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.orders() }),
  });
}

/** On failure the order is read again: it may have changed meanwhile. */
export function useChangeOrderStatus(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (status: OrderStatus) => changeOrderStatus(orderId, status),
    onSuccess: (order) => {
      queryClient.setQueryData(qk.order(orderId), order);
      void queryClient.invalidateQueries({ queryKey: qk.orderList() });
      void queryClient.invalidateQueries({ queryKey: qk.recentOrders() });
    },
    onError: () => queryClient.invalidateQueries({ queryKey: qk.order(orderId) }),
  });
}
