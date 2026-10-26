import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { qk } from '@/shared/api/keys';
import { refreshSession, switchWorkspace } from '@/shared/auth/session';
import { acceptInvitation, createTenant, declineInvitation, getTenant, listReceivedInvitations, listWorkspaces } from './api';

export function useTenant() {
  return useQuery({
    queryKey: qk.tenant(),
    queryFn: ({ signal }) => getTenant(signal),
  });
}

export function useWorkspaces() {
  return useQuery({
    queryKey: qk.workspaces(),
    queryFn: ({ signal }) => listWorkspaces(signal),
  });
}

/**
 * Works in `tenantId` in this tab, then opens its overview. When the token
 * doesn't list it yet (just created or joined), gets a new token first.
 * False if the token still doesn't list it.
 */
export function useGoToWorkspace() {
  const navigate = useNavigate();
  return async (tenantId: string): Promise<boolean> => {
    if (!switchWorkspace(tenantId) && (!(await refreshSession()) || !switchWorkspace(tenantId))) return false;
    await navigate({ to: '/' });
    return true;
  };
}

export function useCreateTenant() {
  return useMutation({ mutationFn: createTenant });
}

export function useReceivedInvitations() {
  return useQuery({
    queryKey: qk.receivedInvitations(),
    queryFn: ({ signal }) => listReceivedInvitations(signal),
  });
}

export function useAcceptInvitation() {
  return useMutation({ mutationFn: acceptInvitation });
}

export function useDeclineInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: declineInvitation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.receivedInvitations() }),
  });
}
