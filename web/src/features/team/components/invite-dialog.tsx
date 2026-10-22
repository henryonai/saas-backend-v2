import { UserPlusIcon } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { InviteRequest } from '@/shared/api/types';
import { useUser } from '@/shared/auth/session';
import { UpgradeNotice } from '@/shared/components/upgrade-notice';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/components/ui/dialog';
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Spinner } from '@/shared/components/ui/spinner';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip';
import { errorMessage, isPlanLimit } from '@/shared/lib/errors';
import { useInvite } from '../hooks';

// The pattern InviteRequest in modules/accounts/src/accounts/schemas.py accepts.
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function InviteDialog() {
  const { t } = useTranslation();
  const { emailVerified } = useUser();
  const invite = useInvite();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<InviteRequest['role']>('member');
  const [emailError, setEmailError] = useState<string | null>(null);

  function reset() {
    setEmail('');
    setRole('member');
    setEmailError(null);
    invite.reset();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (!EMAIL.test(address)) {
      setEmailError(t('team.invite.emailInvalid'));
      return;
    }
    setEmailError(null);
    try {
      await invite.mutateAsync({ email: address, role });
      toast.success(t('team.invite.sent', { email: address }));
      setOpen(false);
      reset();
    } catch {
      // shown in the dialog
    }
  }

  const trigger = (
    <Button disabled={!emailVerified}>
      <UserPlusIcon />
      {t('team.invite.open')}
    </Button>
  );

  // The backend refuses invitations from an unverified address.
  if (!emailVerified) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span tabIndex={0}>{trigger}</span>
        </TooltipTrigger>
        <TooltipContent>{t('team.invite.verifyFirst')}</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} noValidate className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t('team.invite.title')}</DialogTitle>
            <DialogDescription>{t('team.invite.description')}</DialogDescription>
          </DialogHeader>
          {invite.isError &&
            (isPlanLimit(invite.error) ? (
              <UpgradeNotice message={errorMessage(invite.error, t)} />
            ) : (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(invite.error, t)}</AlertDescription>
              </Alert>
            ))}
          <FieldGroup>
            <Field data-invalid={!!emailError}>
              <FieldLabel htmlFor="invite-email">{t('auth.email')}</FieldLabel>
              <Input
                id="invite-email"
                type="email"
                maxLength={254}
                value={email}
                aria-invalid={!!emailError}
                onChange={(event) => setEmail(event.target.value)}
              />
              {emailError && <FieldError>{emailError}</FieldError>}
            </Field>
            <Field>
              <FieldLabel htmlFor="invite-role">{t('team.columns.role')}</FieldLabel>
              <Select value={role} onValueChange={(value) => setRole(value as InviteRequest['role'])}>
                <SelectTrigger id="invite-role" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">{t('roles.member')}</SelectItem>
                  <SelectItem value="admin">{t('roles.admin')}</SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>{t(`team.invite.roleHint.${role ?? 'member'}`)}</FieldDescription>
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={invite.isPending}>
              {invite.isPending && <Spinner />}
              {t('team.invite.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
