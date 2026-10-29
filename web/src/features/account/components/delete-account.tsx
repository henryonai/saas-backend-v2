import { useMutation } from '@tanstack/react-query';
import { UserXIcon } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { api } from '@/shared/api/client';
import { endSession, useUser } from '@/shared/auth/session';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/components/ui/dialog';
import { Field, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Spinner } from '@/shared/components/ui/spinner';
import { errorMessage } from '@/shared/lib/errors';

/** Deletes the login and what is kept about the user (DELETE /account), then signs out. */
function deleteAccount(email: string) {
  return api<void>('/account', { method: 'DELETE', body: { email } });
}

/**
 * The user deletes their account, typing their email to confirm. The API
 * refuses while they own a workspace, and says which: they delete it first.
 */
export function DeleteAccount() {
  const { t } = useTranslation();
  const id = useId();
  const { email } = useUser();
  const remove = useMutation({ mutationFn: deleteAccount });
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [apiError, setApiError] = useState<string | null>(null);
  const matches = typed.trim().toLowerCase() === email.toLowerCase();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setApiError(null);
    try {
      await remove.mutateAsync(typed.trim());
      toast.success(t('account.delete.done'));
      await endSession();
    } catch (caught) {
      setApiError(errorMessage(caught, t));
    }
  }

  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle>{t('account.delete.title')}</CardTitle>
        <CardDescription>{t('account.delete.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <Dialog
          open={open}
          onOpenChange={(next) => {
            if (remove.isPending) return;
            setOpen(next);
            setTyped('');
            setApiError(null);
          }}
        >
          <DialogTrigger asChild>
            <Button variant="destructive">
              <UserXIcon />
              {t('account.delete.open')}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <form onSubmit={submit} className="space-y-4">
              <DialogHeader>
                <DialogTitle>{t('account.delete.confirmTitle')}</DialogTitle>
                <DialogDescription>{t('account.delete.confirmDescription')}</DialogDescription>
              </DialogHeader>
              {apiError && (
                <Alert variant="destructive">
                  <AlertDescription>{apiError}</AlertDescription>
                </Alert>
              )}
              <Field>
                <FieldLabel htmlFor={`${id}-email`}>{t('account.delete.typeEmail', { email })}</FieldLabel>
                <Input id={`${id}-email`} type="email" autoComplete="off" value={typed} onChange={(event) => setTyped(event.target.value)} />
              </Field>
              <DialogFooter>
                <Button type="submit" variant="destructive" disabled={!matches || remove.isPending}>
                  {remove.isPending && <Spinner />}
                  {t('account.delete.submit')}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
