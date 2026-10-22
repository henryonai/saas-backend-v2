import { useMutation, useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { BillingSummary, CheckoutResponse, PaidPlan, Tenant } from '@/shared/api/types';

/** The workspace's subscription; `refetchInterval` while waiting for the payment provider's webhook. */
export function useBilling(refetchInterval?: UseQueryOptions<BillingSummary>['refetchInterval']) {
  return useQuery({
    queryKey: qk.billing(),
    queryFn: ({ signal }) => api<BillingSummary>('/billing', { signal }),
    refetchInterval,
  });
}

/**
 * Waits until the accounts module's copy of the plan (GET /tenants/me) is `plan`.
 * The login trigger puts that copy in new tokens, and a SubscriptionChanged event
 * updates it a few seconds after billing changes: new tokens before then would
 * still carry the old plan, for their whole 15 minutes. False if it never came.
 */
export async function waitForWorkspacePlan(plan: string, { attempts = 30, intervalMs = 2000 } = {}): Promise<boolean> {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if ((await api<Tenant>('/tenants/me')).plan === plan) return true;
    if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}

/** Owner only. Returns the provider's checkout page, where the browser goes next. */
export function useCheckout() {
  return useMutation({
    mutationFn: (plan: PaidPlan) => api<CheckoutResponse>('/billing/checkout', { method: 'POST', body: { plan } }),
  });
}
