import { beforeEach, describe, expect, it, vi } from 'vitest';

const workspace = vi.hoisted(() => ({ getTenant: vi.fn() }));
vi.mock('../workspace/api', () => workspace);

const { waitForWorkspacePlan } = await import('./api');

beforeEach(() => workspace.getTenant.mockReset());

describe('waitForWorkspacePlan', () => {
  it("waits until the accounts module's copy of the plan has changed", async () => {
    workspace.getTenant.mockResolvedValueOnce({ plan: 'free' }).mockResolvedValueOnce({ plan: 'free' }).mockResolvedValueOnce({ plan: 'pro' });
    await expect(waitForWorkspacePlan('pro', { intervalMs: 0 })).resolves.toBe(true);
    expect(workspace.getTenant).toHaveBeenCalledTimes(3);
  });

  it('gives up after its attempts', async () => {
    workspace.getTenant.mockResolvedValue({ plan: 'free' });
    await expect(waitForWorkspacePlan('pro', { attempts: 3, intervalMs: 0 })).resolves.toBe(false);
    expect(workspace.getTenant).toHaveBeenCalledTimes(3);
  });
});
