import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/client';
import { qk } from '@/shared/api/keys';
import type { Invitation, InviteRequest, Member, RoleUpdate } from '@/shared/api/types';

export function useMembers() {
  return useQuery({
    queryKey: qk.members(),
    queryFn: ({ signal }) => api<Member[]>('/users', { signal }),
  });
}

/** Owners and admins; the owner's role can't change. The member is signed out so their next token has the new role. */
export function useChangeRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: RoleUpdate['role'] }) =>
      api<Member>(`/users/${encodeURIComponent(userId)}/role`, { method: 'PATCH', body: { role } }),
    onSuccess: (member) =>
      queryClient.setQueryData<Member[]>(qk.members(), (members) =>
        members?.map((current) => (current.user_id === member.user_id ? member : current)),
      ),
  });
}

/** Removing someone else (owners and admins), or yourself, which means leaving the workspace. */
export function useRemoveMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => api<void>(`/users/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.members() });
      void queryClient.invalidateQueries({ queryKey: qk.tenant() }); // seats used
    },
  });
}

/** Pending invitations this workspace sent. */
export function useSentInvitations() {
  return useQuery({
    queryKey: qk.sentInvitations(),
    queryFn: ({ signal }) => api<Invitation[]>('/users/invitations', { signal }),
  });
}

/** 402 when the plan has no seat left, 409 if already a member or invited, 429 past the daily limit. */
export function useInvite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: InviteRequest) => api<Invitation>('/users/invite', { method: 'POST', body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.sentInvitations() }),
  });
}

export function useCancelInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    // the API decodes the email from the path
    mutationFn: (email: string) => api<void>(`/users/invitations/${encodeURIComponent(email)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.sentInvitations() }),
  });
}
