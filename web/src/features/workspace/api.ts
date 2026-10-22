import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { Invitation, Tenant, TenantCreate } from '@/shared/api/types';

export function useTenant() {
  return useQuery({
    queryKey: qk.tenant(),
    queryFn: ({ signal }) => api<Tenant>('/tenants/me', { signal }),
  });
}

/** Creates the workspace and makes the caller its owner. The token must be refreshed after. */
export function useCreateTenant() {
  return useMutation({
    mutationFn: (body: TenantCreate) => api<Tenant>('/tenants', { method: 'POST', body }),
  });
}

/** Invitations sent to the signed-in user's (verified) email address. */
export function useReceivedInvitations() {
  return useQuery({
    queryKey: qk.receivedInvitations(),
    queryFn: ({ signal }) => api<Invitation[]>('/invitations', { signal }),
  });
}

/** Joins the workspace (takes a seat if the plan has one left). The token must be refreshed after. */
export function useAcceptInvitation() {
  return useMutation({
    mutationFn: (tenantId: string) => api<Tenant>(`/invitations/${encodeURIComponent(tenantId)}/accept`, { method: 'POST' }),
  });
}

export function useDeclineInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tenantId: string) => api<void>(`/invitations/${encodeURIComponent(tenantId)}/decline`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.receivedInvitations() }),
  });
}
