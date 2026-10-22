/**
 * Who the user is, read from the Cognito ID token. The accounts module's login
 * trigger adds the workspace claims (tenant_id, role, plan): all three or none,
 * the same rule as `user_from_claims` in layers/shared/layer/shared/auth.py.
 */
export type Role = 'owner' | 'admin' | 'member';
export const ROLES: readonly Role[] = ['owner', 'admin', 'member'];

export interface SessionUser {
  userId: string;
  email: string;
  emailVerified: boolean;
  /** null until the user creates or joins a workspace, and again after leaving one */
  tenantId: string | null;
  role: Role | null;
  plan: string | null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function isRole(value: string | null): value is Role {
  return value !== null && (ROLES as readonly string[]).includes(value);
}

export function userFromClaims(claims: Record<string, unknown>): SessionUser {
  const tenantId = text(claims.tenant_id);
  const role = text(claims.role);
  const plan = text(claims.plan);
  // Incomplete workspace claims are not a token the backend issues (it answers
  // 401): treat them as no workspace rather than guess.
  const workspace = tenantId && isRole(role) && plan ? { tenantId, role, plan } : null;
  return {
    userId: String(claims.sub ?? ''),
    email: text(claims.email) ?? '',
    emailVerified: claims.email_verified === true || claims.email_verified === 'true',
    tenantId: workspace?.tenantId ?? null,
    role: workspace?.role ?? null,
    plan: workspace?.plan ?? null,
  };
}

/** Owners and admins manage members and invitations (the accounts module checks again). */
export function canManageMembers(role: Role | null): boolean {
  return role === 'owner' || role === 'admin';
}
