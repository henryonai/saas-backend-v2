import { MailIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { Invitation } from '@/shared/api/types';
import { canManageMembers } from '@/shared/auth/claims';
import { useUser } from '@/shared/auth/session';
import { ConfirmDialog } from '@/shared/components/confirm-dialog';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { toastError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import { useCancelInvitation, useSentInvitations } from '../hooks';
import { RoleBadge } from './role-badge';

export function InvitationsTab() {
  const { t } = useTranslation();
  const invitations = useSentInvitations();

  if (invitations.isPending) return <LoadingRows rows={2} />;
  if (invitations.isError) return <ErrorState error={invitations.error} onRetry={() => invitations.refetch()} />;
  if (invitations.data.length === 0) {
    return (
      <Empty className="border border-dashed">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <MailIcon />
          </EmptyMedia>
          <EmptyTitle>{t('team.invitations.emptyTitle')}</EmptyTitle>
          <EmptyDescription>{t('team.invitations.emptyDescription')}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <Card className="py-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('team.columns.email')}</TableHead>
            <TableHead>{t('team.columns.role')}</TableHead>
            <TableHead className="hidden md:table-cell">{t('team.invitations.invitedBy')}</TableHead>
            <TableHead className="hidden sm:table-cell">{t('team.invitations.expires')}</TableHead>
            <TableHead className="w-0">
              <span className="sr-only">{t('team.columns.actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {invitations.data.map((invitation) => (
            <InvitationRow key={invitation.email} invitation={invitation} />
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function InvitationRow({ invitation }: { invitation: Invitation }) {
  const { t } = useTranslation();
  const { role } = useUser();
  const cancel = useCancelInvitation();

  async function onCancel() {
    try {
      await cancel.mutateAsync(invitation.email);
      toast.success(t('team.invitations.cancelled', { email: invitation.email }));
    } catch (error) {
      toastError(error, t);
      throw error;
    }
  }

  return (
    <TableRow>
      <TableCell className="font-medium">{invitation.email}</TableCell>
      <TableCell>
        <RoleBadge role={invitation.role} />
      </TableCell>
      <TableCell className="hidden text-muted-foreground md:table-cell">{invitation.invited_by}</TableCell>
      <TableCell className="hidden text-muted-foreground sm:table-cell">{formatDate(invitation.expires_at)}</TableCell>
      <TableCell>
        {canManageMembers(role) && (
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="icon-sm" aria-label={t('team.invitations.cancel')}>
                <XIcon />
              </Button>
            }
            title={t('team.invitations.cancelTitle', { email: invitation.email })}
            description={t('team.invitations.cancelDescription')}
            confirmLabel={t('team.invitations.cancel')}
            destructive
            onConfirm={onCancel}
          />
        )}
      </TableCell>
    </TableRow>
  );
}
