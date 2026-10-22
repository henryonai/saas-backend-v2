import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { qk } from '@/shared/api/keys';
import type { Member, RoleUpdate } from '@/shared/api/types';
import { cancelInvitation, changeRole, invite, listMembers, listSentInvitations, removeMember } from './api';

export function useMembers() {
  return useQuery({
    queryKey: qk.members(),
    queryFn: ({ signal }) => listMembers(signal),
  });
}

export function useChangeRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: RoleUpdate['role'] }) => changeRole(userId, role),
    onSuccess: (member) =>
      queryClient.setQueryData<Member[]>(qk.members(), (members) =>
        members?.map((current) => (current.user_id === member.user_id ? member : current)),
      ),
  });
}

export function useRemoveMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: removeMember,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.members() });
      void queryClient.invalidateQueries({ queryKey: qk.tenant() }); // seats used
    },
  });
}

export function useSentInvitations() {
  return useQuery({
    queryKey: qk.sentInvitations(),
    queryFn: ({ signal }) => listSentInvitations(signal),
  });
}

export function useInvite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: invite,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.sentInvitations() }),
  });
}

export function useCancelInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cancelInvitation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.sentInvitations() }),
  });
}
