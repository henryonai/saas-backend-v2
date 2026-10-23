import { api } from '@/shared/api/client';
import type { BillingSummary, CheckoutResponse, PaidPlan, PortalResponse } from '@/shared/api/types';
import { getTenant } from '../workspace/api';

/** The workspace's subscription, with its plan's price and limits. */
export function getBilling(signal?: AbortSignal) {
  return api<BillingSummary>('/billing', { signal });
}

/**
 * Owner only. Returns the provider's page, where the browser goes next: a
 * checkout, or for a subscriber, the page that confirms a plan change.
 */
export function startCheckout(plan: PaidPlan) {
  return api<CheckoutResponse>('/billing/checkout', { method: 'POST', body: { plan } });
}

/** Owner only, when `manageable`. The provider's page for the card, invoices and cancelling. */
export function openPortal() {
  return api<PortalResponse>('/billing/portal', { method: 'POST' });
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
