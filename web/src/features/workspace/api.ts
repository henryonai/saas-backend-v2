import { api } from '@/shared/api/client';
import type { Invitation, Tenant, TenantCreate, WorkspaceSummary } from '@/shared/api/types';

/** The workspace this tab works in. */
export function getTenant(signal?: AbortSignal) {
  return api<Tenant>('/tenants/me', { signal });
}

/** Every workspace the signed-in user belongs to, by name. */
export function listWorkspaces(signal?: AbortSignal) {
  return api<WorkspaceSummary[]>('/tenants', { signal });
}

/**
 * Creates a workspace and makes the caller its owner. The token must be
 * refreshed after. Send the same `idempotencyKey` again to retry: the API
 * returns the workspace the first request made instead of making another.
 */
export function createTenant(body: TenantCreate, idempotencyKey: string) {
  return api<Tenant>('/tenants', { method: 'POST', body, headers: { 'Idempotency-Key': idempotencyKey } });
}

/** Invitations sent to the signed-in user's (verified) email address. */
export function listReceivedInvitations(signal?: AbortSignal) {
  return api<Invitation[]>('/invitations', { signal });
}

/** Joins the workspace (takes a seat if the plan has one left). The token must be refreshed after. */
export function acceptInvitation(tenantId: string) {
  return api<Tenant>(`/invitations/${encodeURIComponent(tenantId)}/accept`, { method: 'POST' });
}

export function declineInvitation(tenantId: string) {
  return api<void>(`/invitations/${encodeURIComponent(tenantId)}/decline`, { method: 'POST' });
}
