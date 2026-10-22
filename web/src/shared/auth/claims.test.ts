import { describe, expect, it } from 'vitest';
import { canManageMembers, userFromClaims } from './claims';

const base = { sub: 'user-1', email: 'a@example.com', email_verified: true, token_use: 'id' };

describe('userFromClaims', () => {
  it('reads a user with a workspace', () => {
    expect(userFromClaims({ ...base, tenant_id: 't-1', role: 'admin', plan: 'pro' })).toEqual({
      userId: 'user-1',
      email: 'a@example.com',
      emailVerified: true,
      tenantId: 't-1',
      role: 'admin',
      plan: 'pro',
    });
  });

  it('reads a user without a workspace (right after sign-up)', () => {
    expect(userFromClaims(base)).toMatchObject({ tenantId: null, role: null, plan: null });
  });

  it('ignores incomplete or unknown workspace claims, as the backend refuses them', () => {
    expect(userFromClaims({ ...base, tenant_id: 't-1', role: 'admin' })).toMatchObject({ tenantId: null });
    expect(userFromClaims({ ...base, tenant_id: 't-1', role: 'superuser', plan: 'pro' })).toMatchObject({ tenantId: null });
  });

  it('accepts email_verified as a boolean or a string', () => {
    expect(userFromClaims({ ...base, email_verified: 'true' }).emailVerified).toBe(true);
    expect(userFromClaims({ ...base, email_verified: false }).emailVerified).toBe(false);
    expect(userFromClaims({ sub: 'u' }).emailVerified).toBe(false);
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
