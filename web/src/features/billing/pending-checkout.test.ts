import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearPendingCheckout, readPendingCheckout, savePendingCheckout } from './pending-checkout';

afterEach(() => vi.useRealTimers());

describe('pending checkout', () => {
  it('is remembered for the workspace that started it', () => {
    savePendingCheckout('t-1', 'pro');
    expect(readPendingCheckout('t-1')).toMatchObject({ tenantId: 't-1', plan: 'pro' });
    expect(readPendingCheckout('t-2')).toBeNull();
  });

  it('expires after an hour', () => {
    vi.useFakeTimers();
    savePendingCheckout('t-1', 'pro');
    vi.advanceTimersByTime(61 * 60 * 1000);
    expect(readPendingCheckout('t-1')).toBeNull();
  });

  it('can be cleared', () => {
    savePendingCheckout('t-1', 'business');
    clearPendingCheckout();
    expect(readPendingCheckout('t-1')).toBeNull();
  });
});
