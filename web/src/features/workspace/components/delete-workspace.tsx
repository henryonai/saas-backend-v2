import { useNavigate } from '@tanstack/react-router';
import { Trash2Icon } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { refreshSession, useUser } from '@/shared/auth/session';
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
import { formatDateTime } from '@/shared/lib/format';
import { useDeleteTenant, useTenant } from '../hooks';

/**
 * The owner deletes the workspace, typing its name to confirm. It leaves
 * everyone's workspace list at once and can be restored from the switcher
 * until it is purged; the tab moves to another workspace, or to the welcome page.
 */
export function DeleteWorkspace() {
  const { t } = useTranslation();
  const id = useId();
  const navigate = useNavigate();
  const { role } = useUser();
  const tenant = useTenant();
  const remove = useDeleteTenant();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [apiError, setApiError] = useState<string | null>(null);
  if (role !== 'owner' || !tenant.data) return null;
  const name = tenant.data.name;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setApiError(null);
    try {
      const deleted = await remove.mutateAsync(typed);
      toast.success(t('workspaces.delete.done', { workspace: name, date: formatDateTime(deleted.delete_after ?? Date.now() / 1000) }));
      setOpen(false);
      const user = await refreshSession();
      if (user) await navigate({ to: user.tenantId ? '/' : '/welcome' });
    } catch (caught) {
      setApiError(errorMessage(caught, t));
    }
  }

  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle>{t('workspaces.delete.title')}</CardTitle>
        <CardDescription>{t('workspaces.delete.description')}</CardDescription>
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
              <Trash2Icon />
              {t('workspaces.delete.open')}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <form onSubmit={submit} className="space-y-4">
              <DialogHeader>
                <DialogTitle>{t('workspaces.delete.confirmTitle', { workspace: name })}</DialogTitle>
                <DialogDescription>{t('workspaces.delete.confirmDescription')}</DialogDescription>
              </DialogHeader>
              {apiError && (
                <Alert variant="destructive">
                  <AlertDescription>{apiError}</AlertDescription>
                </Alert>
              )}
              <Field>
                <FieldLabel htmlFor={`${id}-name`}>{t('workspaces.delete.typeName', { workspace: name })}</FieldLabel>
                <Input id={`${id}-name`} autoComplete="off" value={typed} onChange={(event) => setTyped(event.target.value)} />
              </Field>
              <DialogFooter>
                <Button type="submit" variant="destructive" disabled={typed !== name || remove.isPending}>
                  {remove.isPending && <Spinner />}
                  {t('workspaces.delete.submit')}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
