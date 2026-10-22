import { useNavigate } from '@tanstack/react-router';
import { MailIcon, PlusIcon } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { Invitation } from '@/shared/api/types';
import { refreshSession, useUser } from '@/shared/auth/session';
import { LoadingRows, ErrorState } from '@/shared/components/query-state';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from '@/shared/components/ui/empty';
import { Field, FieldError, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Spinner } from '@/shared/components/ui/spinner';
import { errorMessage, toastError } from '@/shared/lib/errors';
import { formatDate } from '@/shared/lib/format';
import { useAcceptInvitation, useCreateTenant, useDeclineInvitation, useReceivedInvitations } from './api';

/**
 * A new token, now that the user belongs to a workspace: the login trigger adds
 * tenant_id, role and plan to it, and the app's routes then let them in.
 */
async function enterWorkspace(navigate: ReturnType<typeof useNavigate>): Promise<void> {
  const user = await refreshSession();
  if (user?.tenantId) await navigate({ to: '/' });
}

export function WelcomePage() {
  const { t } = useTranslation();
  const { email } = useUser();
  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">{t('welcome.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('welcome.description', { email })}</p>
      </div>
      <ReceivedInvitations />
      <CreateWorkspace />
    </div>
  );
}

function ReceivedInvitations() {
  const { t } = useTranslation();
  const invitations = useReceivedInvitations();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('welcome.invitations.title')}</CardTitle>
        <CardDescription>{t('welcome.invitations.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        {invitations.isPending ? (
          <LoadingRows rows={2} />
        ) : invitations.isError ? (
          <ErrorState error={invitations.error} onRetry={() => invitations.refetch()} />
        ) : invitations.data.length === 0 ? (
          <Empty className="border border-dashed p-6">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <MailIcon />
              </EmptyMedia>
              <EmptyDescription>{t('welcome.invitations.empty')}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="divide-y">
            {invitations.data.map((invitation) => (
              <InvitationRow key={invitation.tenant_id} invitation={invitation} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function InvitationRow({ invitation }: { invitation: Invitation }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const accept = useAcceptInvitation();
  const decline = useDeclineInvitation();
  const busy = accept.isPending || decline.isPending;

  async function onAccept() {
    try {
      const tenant = await accept.mutateAsync(invitation.tenant_id);
      toast.success(t('welcome.invitations.accepted', { workspace: tenant.name }));
      await enterWorkspace(navigate);
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

function CreateWorkspace() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreateTenant();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t('welcome.create.nameRequired'));
      return;
    }
    setError(null);
    try {
      await create.mutateAsync({ name: trimmed });
      toast.success(t('welcome.create.created', { workspace: trimmed }));
      await enterWorkspace(navigate);
    } catch (caught) {
      setError(errorMessage(caught, t));
    }
  }

  return (
    <Card>
      <form onSubmit={submit} noValidate>
        <CardHeader>
          <CardTitle>{t('welcome.create.title')}</CardTitle>
          <CardDescription>{t('welcome.create.description')}</CardDescription>
        </CardHeader>
        <CardContent className="mt-4">
          <Field data-invalid={!!error}>
            <FieldLabel htmlFor="workspace-name">{t('welcome.create.name')}</FieldLabel>
            <Input
              id="workspace-name"
              maxLength={100}
              placeholder={t('welcome.create.namePlaceholder')}
              value={name}
              aria-invalid={!!error}
              onChange={(event) => setName(event.target.value)}
            />
            {error && <FieldError>{error}</FieldError>}
          </Field>
        </CardContent>
        <CardFooter className="mt-4 justify-end">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? <Spinner /> : <PlusIcon />}
            {t('welcome.create.submit')}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
