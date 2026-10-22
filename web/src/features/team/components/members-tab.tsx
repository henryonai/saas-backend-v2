import { useNavigate } from '@tanstack/react-router';
import { LogOutIcon, Trash2Icon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { Member } from '@/shared/api/types';
import { canManageMembers, type Role } from '@/shared/auth/claims';
import { endSession, useUser } from '@/shared/auth/session';
import { ConfirmDialog } from '@/shared/components/confirm-dialog';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { toastError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import { useChangeRole, useMembers, useRemoveMember } from '../hooks';
import { RoleBadge } from './role-badge';

const ORDER: Record<Role, number> = { owner: 0, admin: 1, member: 2 };

export function MembersTab() {
  const { t } = useTranslation();
  const members = useMembers();

  if (members.isPending) return <LoadingRows rows={3} />;
  if (members.isError) return <ErrorState error={members.error} onRetry={() => members.refetch()} />;

  const sorted = [...members.data].sort((a, b) => ORDER[a.role] - ORDER[b.role] || a.email.localeCompare(b.email));
  return (
    <div className="space-y-4">
      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('team.columns.email')}</TableHead>
              <TableHead>{t('team.columns.role')}</TableHead>
              <TableHead className="hidden sm:table-cell">{t('team.columns.joined')}</TableHead>
              <TableHead className="w-0">
                <span className="sr-only">{t('team.columns.actions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((member) => (
              <MemberRow key={member.user_id} member={member} />
            ))}
          </TableBody>
        </Table>
      </Card>
      <LeaveWorkspace />
    </div>
  );
}

function MemberRow({ member }: { member: Member }) {
  const { t } = useTranslation();
  const me = useUser();
  const changeRole = useChangeRole();
  const remove = useRemoveMember();
  const isMe = member.user_id === me.userId;
  // The owner can't be changed or removed; you leave rather than remove yourself.
  const manageable = canManageMembers(me.role) && member.role !== 'owner' && !isMe;

  async function onRoleChange(role: string) {
    try {
      await changeRole.mutateAsync({ userId: member.user_id, role: role as 'admin' | 'member' });
      toast.success(t('team.roleChanged', { email: member.email, role: t(`roles.${role as Role}`) }));
    } catch (error) {
      toastError(error, t);
    }
  }

  async function onRemove() {
    try {
      await remove.mutateAsync(member.user_id);
      toast.success(t('team.removed', { email: member.email }));
    } catch (error) {
      toastError(error, t);
      throw error;
    }
  }

  return (
    <TableRow>
      <TableCell className="font-medium">
        <span className="flex items-center gap-2">
          <span className="truncate">{member.email}</span>
          {isMe && <Badge variant="outline">{t('common.you')}</Badge>}
        </span>
      </TableCell>
      <TableCell>
        {manageable ? (
          <Select value={member.role} onValueChange={onRoleChange} disabled={changeRole.isPending}>
            <SelectTrigger size="sm" className="w-32" aria-label={t('team.columns.role')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="admin">{t('roles.admin')}</SelectItem>
              <SelectItem value="member">{t('roles.member')}</SelectItem>
            </SelectContent>
          </Select>
        ) : (
          <RoleBadge role={member.role} />
        )}
      </TableCell>
      <TableCell className="hidden text-muted-foreground sm:table-cell">{formatDate(member.created_at)}</TableCell>
      <TableCell>
        {manageable && (
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="icon-sm" aria-label={t('team.remove')}>
                <Trash2Icon />
              </Button>
            }
            title={t('team.removeTitle', { email: member.email })}
            description={t('team.removeDescription')}
            confirmLabel={t('team.remove')}
            destructive
            onConfirm={onRemove}
          />
        )}
      </TableCell>
    </TableRow>
  );
}

/** Anyone but the owner can leave. Leaving revokes the session, so the user signs in again. */
function LeaveWorkspace() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const me = useUser();
  const remove = useRemoveMember();
  if (me.role === 'owner') return null;

  async function leave() {
    try {
      await remove.mutateAsync(me.userId);
    } catch (error) {
      toastError(error, t);
      throw error;
    }
    await endSession('left');
    await navigate({ to: '/sign-in' });
  }

  return (
    <div className="flex justify-end">
      <ConfirmDialog
        trigger={
          <Button variant="outline">
            <LogOutIcon />
            {t('team.leave')}
          </Button>
        }
        title={t('team.leaveTitle')}
        description={t('team.leaveDescription')}
        confirmLabel={t('team.leave')}
        destructive
        onConfirm={leave}
      />
    </div>
  );
}
