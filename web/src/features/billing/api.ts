import { api } from '@/shared/api/client';
import type { BillingSummary, CheckoutResponse, PaidPlan } from '@/shared/api/types';
import { getTenant } from '../workspace/api';

/** The workspace's subscription, with its plan's price and limits. */
export function getBilling(signal?: AbortSignal) {
  return api<BillingSummary>('/billing', { signal });
}

/** Owner only. Returns the provider's checkout page, where the browser goes next. */
export function startCheckout(plan: PaidPlan) {
  return api<CheckoutResponse>('/billing/checkout', { method: 'POST', body: { plan } });
}

/**
 * Waits until the accounts module's copy of the plan (GET /tenants/me) is `plan`.
 * The login trigger puts that copy in new tokens, and a SubscriptionChanged event
 * updates it a few seconds after billing changes: new tokens before then would
 * still carry the old plan, for their whole 15 minutes. False if it never came.
 */
export async function waitForWorkspacePlan(plan: string, { attempts = 30, intervalMs = 2000 } = {}): Promise<boolean> {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if ((await getTenant()).plan === plan) return true;
    if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}
