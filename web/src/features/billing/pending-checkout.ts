/**
 * The plan the owner went to pay for. The provider tells the backend (webhook),
 * not the browser, so on return the billing page waits until /billing shows it.
 * Kept in sessionStorage: it only matters for this tab, and only for a while.
 */
const KEY = 'saas.pendingCheckout';
const MAX_AGE_MS = 60 * 60 * 1000;

export interface PendingCheckout {
  tenantId: string;
  plan: string;
  startedAt: number;
}

export function savePendingCheckout(tenantId: string, plan: string): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ tenantId, plan, startedAt: Date.now() } satisfies PendingCheckout));
  } catch {
    // storage disabled: the page just won't wait for the change
  }
}

export function readPendingCheckout(tenantId: string | null): PendingCheckout | null {
  try {
    const pending = JSON.parse(sessionStorage.getItem(KEY) ?? 'null') as PendingCheckout | null;
    if (pending && pending.tenantId === tenantId && Date.now() - pending.startedAt < MAX_AGE_MS) return pending;
  } catch {
    // unreadable: forget it
  }
  clearPendingCheckout();
  return null;
}

export function clearPendingCheckout(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // storage disabled
  }
}
