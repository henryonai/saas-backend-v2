import { useMutation, useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { BillingSummary, CheckoutResponse, PaidPlan } from '@/shared/api/types';

/** The workspace's subscription; `refetchInterval` while waiting for the payment provider's webhook. */
export function useBilling(refetchInterval?: UseQueryOptions<BillingSummary>['refetchInterval']) {
  return useQuery({
    queryKey: qk.billing(),
    queryFn: ({ signal }) => api<BillingSummary>('/billing', { signal }),
    refetchInterval,
  });
}

/** Owner only. Returns the provider's checkout page, where the browser goes next. */
export function useCheckout() {
  return useMutation({
    mutationFn: (plan: PaidPlan) => api<CheckoutResponse>('/billing/checkout', { method: 'POST', body: { plan } }),
  });
}
