import { MailIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { Invitation } from '@/shared/api/types';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from '@/shared/components/ui/empty';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import { useAcceptInvitation, useDeclineInvitation, useGoToWorkspace, useReceivedInvitations } from '../hooks';

/** Invitations sent to the user's email address. Accepting one joins that workspace and opens it. */
export function InvitationList({ onJoined }: { onJoined?: () => void }) {
  const { t } = useTranslation();
  const invitations = useReceivedInvitations();

  if (invitations.isPending) return <LoadingRows rows={2} />;
  if (invitations.isError) return <ErrorState error={invitations.error} onRetry={() => invitations.refetch()} />;
  if (invitations.data.length === 0) {
    return (
      <Empty className="border border-dashed p-6">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <MailIcon />
          </EmptyMedia>
          <EmptyDescription>{t('welcome.invitations.empty')}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return (
    <ul className="divide-y">
      {invitations.data.map((invitation) => (
        <InvitationRow key={invitation.tenant_id} invitation={invitation} onJoined={onJoined} />
      ))}
    </ul>
  );
}

function InvitationRow({ invitation, onJoined }: { invitation: Invitation; onJoined?: () => void }) {
  const { t } = useTranslation();
  const accept = useAcceptInvitation();
  const decline = useDeclineInvitation();
  const goTo = useGoToWorkspace();
  const busy = accept.isPending || decline.isPending;

  async function onAccept() {
    try {
      const tenant = await accept.mutateAsync(invitation.tenant_id);
      toast.success(t('welcome.invitations.accepted', { workspace: tenant.name }));
      onJoined?.();
      await goTo(tenant.tenant_id);
    } catch (error) {
      toastError(error, t);
    }
  }

  async function onDecline() {
    try {
      await decline.mutateAsync(invitation.tenant_id);
    } catch (error) {
      toastError(error, t);
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 space-y-0.5">
        {/* the workspace name was typed by the inviter: shown as plain text, quoted */}
        <p className="font-medium">“{invitation.tenant_name}”</p>
        <p className="text-sm text-muted-foreground">
          {t('welcome.invitations.from', { inviter: invitation.invited_by, role: t(`roles.${invitation.role}`) })}
          {' · '}
          {t('welcome.invitations.expires', { date: formatDate(invitation.expires_at) })}
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onDecline} disabled={busy}>
          {decline.isPending && <Spinner />}
          {t('welcome.invitations.decline')}
        </Button>
        <Button onClick={onAccept} disabled={busy}>
          {accept.isPending && <Spinner />}
          {t('welcome.invitations.accept')}
        </Button>
      </div>
    </li>
  );
}
