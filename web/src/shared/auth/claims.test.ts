import { describe, expect, it } from 'vitest';
import { canManageMembers, inWorkspace, userFromClaims } from './claims';

const base = { sub: 'user-1', email: 'a@example.com', email_verified: true, token_use: 'id' };
const two = {
  ...base,
  workspaces: JSON.stringify({ 't-1': { role: 'admin', plan: 'pro' }, 't-2': { role: 'member', plan: 'free' } }),
};

describe('userFromClaims', () => {
  it('reads every workspace the token lists, working in the first by default', () => {
    expect(userFromClaims(two)).toEqual({
      userId: 'user-1',
      email: 'a@example.com',
      emailVerified: true,
      workspaces: [
        { tenantId: 't-1', role: 'admin', plan: 'pro' },
        { tenantId: 't-2', role: 'member', plan: 'free' },
      ],
      tenantId: 't-1',
      role: 'admin',
      plan: 'pro',
    });
  });

  it('works in the workspace the tab chose, while the token lists it', () => {
    expect(userFromClaims(two, 't-2')).toMatchObject({ tenantId: 't-2', role: 'member', plan: 'free' });
    expect(userFromClaims(two, 't-gone')).toMatchObject({ tenantId: 't-1' }); // left or removed: the first
  });

  it('reads a user without a workspace (right after sign-up)', () => {
    expect(userFromClaims(base, 't-1')).toMatchObject({ workspaces: [], tenantId: null, role: null, plan: null });
  });

  it('ignores a workspaces claim the backend would refuse', () => {
    for (const workspaces of ['not json', '["t-1"]', '{"t-1": {"role": "admin"}}', '{"t-1": {"role": "superuser", "plan": "pro"}}']) {
      expect(userFromClaims({ ...base, workspaces })).toMatchObject({ workspaces: [], tenantId: null });
    }
  });

  it('accepts email_verified as a boolean or a string', () => {
    expect(userFromClaims({ ...base, email_verified: 'true' }).emailVerified).toBe(true);
    expect(userFromClaims({ ...base, email_verified: false }).emailVerified).toBe(false);
    expect(userFromClaims({ sub: 'u' }).emailVerified).toBe(false);
  });
});

describe('inWorkspace', () => {
  it('switches the role and plan with the workspace', () => {
    expect(inWorkspace(userFromClaims(two), 't-2')).toMatchObject({ tenantId: 't-2', role: 'member', plan: 'free' });
  });
});

describe('canManageMembers', () => {
  it('lets owners and admins manage members', () => {
    expect(canManageMembers('owner')).toBe(true);
    expect(canManageMembers('admin')).toBe(true);
    expect(canManageMembers('member')).toBe(false);
    expect(canManageMembers(null)).toBe(false);
  });
});
