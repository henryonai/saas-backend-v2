import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import { acceptInvitation, createTenant, declineInvitation, getTenant, listReceivedInvitations } from './api';

export function useTenant() {
  return useQuery({
    queryKey: qk.tenant(),
    queryFn: ({ signal }) => getTenant(signal),
  });
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
