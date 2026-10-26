/**
 * Who the user is, read from the Cognito ID token. The accounts module's login
 * trigger adds one claim, `workspaces`: a JSON string with every workspace the
 * user belongs to, their role there and its plan. The same rules as
 * `workspaces_in` in layers/shared/layer/shared/auth.py.
 *
 * A user can work in a different workspace in each browser tab: `tenantId`,
 * `role` and `plan` are the one this tab works in (session.ts chooses it),
 * and the app sends it with every request (X-Tenant-Id).
 */
export type Role = 'owner' | 'admin' | 'member';
export const ROLES: readonly Role[] = ['owner', 'admin', 'member'];

export interface Workspace {
  tenantId: string;
  role: Role;
  plan: string;
}

export interface SessionUser {
  userId: string;
  email: string;
  emailVerified: boolean;
  /** every workspace the token lists; empty until the user creates or joins one */
  workspaces: Workspace[];
  /** the workspace this tab works in, one of `workspaces`; null when there is none */
  tenantId: string | null;
  role: Role | null;
  plan: string | null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function isRole(value: unknown): value is Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value);
}

/** The token's workspaces. A claim the backend wouldn't accept (it answers 401) reads as none. */
export function workspacesFromClaims(claims: Record<string, unknown>): Workspace[] {
  const raw = text(claims.workspaces);
  if (!raw) return [];
  let listed: unknown;
  try {
    listed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!listed || typeof listed !== 'object' || Array.isArray(listed)) return [];
  const workspaces: Workspace[] = [];
  for (const [tenantId, value] of Object.entries(listed as Record<string, unknown>)) {
    const { role, plan } = (value ?? {}) as { role?: unknown; plan?: unknown };
    if (!isRole(role) || !text(plan)) return [];
    workspaces.push({ tenantId, role, plan: plan as string });
  }
  return workspaces;
}

/** The user as the token describes them, working in `tenantId` if the token lists it, else its first workspace. */
export function userFromClaims(claims: Record<string, unknown>, tenantId: string | null = null): SessionUser {
  const user = {
    userId: String(claims.sub ?? ''),
    email: text(claims.email) ?? '',
    emailVerified: claims.email_verified === true || claims.email_verified === 'true',
    workspaces: workspacesFromClaims(claims),
  };
  return inWorkspace(user, tenantId);
}

/** The same user working in `tenantId`, or in their first workspace if it isn't one of theirs. */
export function inWorkspace(user: Omit<SessionUser, 'tenantId' | 'role' | 'plan'>, tenantId: string | null): SessionUser {
  const current = user.workspaces.find((w) => w.tenantId === tenantId) ?? user.workspaces[0] ?? null;
  return { ...user, tenantId: current?.tenantId ?? null, role: current?.role ?? null, plan: current?.plan ?? null };
}

/** Owners and admins manage members and invitations (the accounts module checks again). */
export function canManageMembers(role: Role | null): boolean {
  return role === 'owner' || role === 'admin';
}
