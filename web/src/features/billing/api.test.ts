import { beforeEach, describe, expect, it, vi } from 'vitest';

const client = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/shared/api/client', () => client);

const { waitForWorkspacePlan } = await import('./api');

beforeEach(() => client.api.mockReset());

describe('waitForWorkspacePlan', () => {
  it("waits until the accounts module's copy of the plan has changed", async () => {
    client.api.mockResolvedValueOnce({ plan: 'free' }).mockResolvedValueOnce({ plan: 'free' }).mockResolvedValueOnce({ plan: 'pro' });
    await expect(waitForWorkspacePlan('pro', { intervalMs: 0 })).resolves.toBe(true);
    expect(client.api).toHaveBeenCalledTimes(3);
    expect(client.api).toHaveBeenCalledWith('/tenants/me');
  });

  it('gives up after its attempts', async () => {
    client.api.mockResolvedValue({ plan: 'free' });
    await expect(waitForWorkspacePlan('pro', { attempts: 3, intervalMs: 0 })).resolves.toBe(false);
    expect(client.api).toHaveBeenCalledTimes(3);
  });
});
