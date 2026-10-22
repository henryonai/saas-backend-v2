import { api } from '@/shared/api/client';
import type { Invitation, InviteRequest, Member, RoleUpdate } from '@/shared/api/types';

export function listMembers(signal?: AbortSignal) {
  return api<Member[]>('/users', { signal });
}

/** Owners and admins; the owner's role can't change. The member is signed out so their next token has the new role. */
export function changeRole(userId: string, role: RoleUpdate['role']) {
  return api<Member>(`/users/${encodeURIComponent(userId)}/role`, { method: 'PATCH', body: { role } });
}

/** Removing someone else (owners and admins), or yourself, which means leaving the workspace. */
export function removeMember(userId: string) {
  return api<void>(`/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
}

/** Pending invitations this workspace sent. */
export function listSentInvitations(signal?: AbortSignal) {
  return api<Invitation[]>('/users/invitations', { signal });
}

/** 402 when the plan has no seat left, 409 if already a member or invited, 429 past the daily limit. */
export function invite(body: InviteRequest) {
  return api<Invitation>('/users/invite', { method: 'POST', body });
}

/** The API decodes the email from the path. */
export function cancelInvitation(email: string) {
  return api<void>(`/users/invitations/${encodeURIComponent(email)}`, { method: 'DELETE' });
}
