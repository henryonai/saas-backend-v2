import { useMutation, useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import type { BillingSummary } from '@/shared/api/types';
import { getBilling, startCheckout } from './api';

/** `refetchInterval` while waiting for the payment provider's webhook. */
export function useBilling(refetchInterval?: UseQueryOptions<BillingSummary>['refetchInterval']) {
  return useQuery({
    queryKey: qk.billing(),
    queryFn: ({ signal }) => getBilling(signal),
    refetchInterval,
  });
}

export function useCheckout() {
  return useMutation({ mutationFn: startCheckout });
}
