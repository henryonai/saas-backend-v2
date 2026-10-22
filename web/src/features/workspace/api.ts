import { api } from '@/shared/api/client';
import type { Invitation, Tenant, TenantCreate } from '@/shared/api/types';

/** The caller's workspace. */
export function getTenant(signal?: AbortSignal) {
  return api<Tenant>('/tenants/me', { signal });
}

/** Creates the workspace and makes the caller its owner. The token must be refreshed after. */
export function createTenant(body: TenantCreate) {
  return api<Tenant>('/tenants', { method: 'POST', body });
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
